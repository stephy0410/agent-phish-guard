"""Check what the agent role and a developer can and cannot do, with the IAM policy simulator.

    AWS_PROFILE=phishguard AWS_REGION=us-east-1 uv run python infra/verify_permissions.py

Run after `terraform apply`. The simulator evaluates the policies attached in the account, so
nothing is invoked and nothing costs money. Exits non-zero if any decision is unexpected.
"""

from __future__ import annotations

import sys

import boto3

HAIKU = "anthropic.claude-haiku-4-5-20251001-v1:0"
OPUS = "anthropic.claude-opus-4-6-v1"
NOT_ALLOWED = "anthropic.claude-sonnet-4-5-20250929-v1:0"  # any model the agent does not use
REGION = "us-east-1"


def main() -> int:
    iam = boto3.client("iam")
    account = boto3.client("sts").get_caller_identity()["Account"]
    role = f"arn:aws:iam::{account}:role/agent-phish-guard-agent"
    ale = f"arn:aws:iam::{account}:user/phishguard/ale"
    hannah = f"arn:aws:iam::{account}:user/phishguard/hannah"

    def profile(model: str) -> str:
        return f"arn:aws:bedrock:{REGION}:{account}:inference-profile/us.{model}"

    def model(region: str, model_id: str) -> str:
        return f"arn:aws:bedrock:{region}::foundation-model/{model_id}"

    def through(profile_arn: str) -> list[dict]:
        return [_context("bedrock:InferenceProfileArn", profile_arn)]

    as_ale = [_context("aws:username", "ale")]
    # The OAuth2 client `aws login` signs in through.
    cli_client = f"arn:aws:signin:{REGION}:{account}:oauth2/public-client/localhost"

    cases = [
        # (description, principal, action, resource, context, expected)
        ("role: Haiku profile", role, "bedrock:InvokeModel", profile(HAIKU), [], True),
        (
            "role: Opus profile, streaming",
            role,
            "bedrock:InvokeModelWithResponseStream",
            profile(OPUS),
            [],
            True,
        ),
        (
            "role: Haiku in us-east-2 through its profile",
            role,
            "bedrock:InvokeModel",
            model("us-east-2", HAIKU),
            through(profile(HAIKU)),
            True,
        ),
        (
            "role: Opus in us-west-2 through its profile",
            role,
            "bedrock:InvokeModel",
            model("us-west-2", OPUS),
            through(profile(OPUS)),
            True,
        ),
        (
            "role: Haiku model directly, no profile",
            role,
            "bedrock:InvokeModel",
            model("us-east-2", HAIKU),
            [],
            False,
        ),
        (
            "role: another model's profile",
            role,
            "bedrock:InvokeModel",
            profile(NOT_ALLOWED),
            [],
            False,
        ),
        (
            "role: another model through the Haiku profile",
            role,
            "bedrock:InvokeModel",
            model(REGION, NOT_ALLOWED),
            through(profile(HAIKU)),
            False,
        ),
        (
            "role: Haiku model through another profile",
            role,
            "bedrock:InvokeModel",
            model(REGION, HAIKU),
            through(profile(NOT_ALLOWED)),
            False,
        ),
        ("role: list S3 buckets", role, "s3:ListAllMyBuckets", "*", [], False),
        ("role: create IAM users", role, "iam:CreateUser", "*", [], False),
        ("ale: Haiku profile", ale, "bedrock:InvokeModel", profile(HAIKU), [], True),
        ("ale: list models", ale, "bedrock:ListFoundationModels", "*", [], True),
        ("ale: change own password", ale, "iam:ChangePassword", ale, as_ale, True),
        ("ale: change Hannah's password", ale, "iam:ChangePassword", hannah, as_ale, False),
        (
            "ale: another model's profile",
            ale,
            "bedrock:InvokeModel",
            profile(NOT_ALLOWED),
            [],
            False,
        ),
        ("ale: create an access key", ale, "iam:CreateAccessKey", ale, as_ale, False),
        ("ale: attach a policy to self", ale, "iam:AttachUserPolicy", ale, as_ale, False),
        ("ale: aws login, authorize", ale, "signin:AuthorizeOAuth2Access", cli_client, [], True),
        ("ale: aws login, get CLI token", ale, "signin:CreateOAuth2Token", cli_client, [], True),
        ("role: aws login", role, "signin:CreateOAuth2Token", cli_client, [], False),
    ]

    failures = 0
    for description, principal, action, resource, context, expected in cases:
        result = iam.simulate_principal_policy(
            PolicySourceArn=principal,
            ActionNames=[action],
            ResourceArns=[resource],
            ContextEntries=context,
        )["EvaluationResults"][0]["EvalDecision"]
        passed = (result == "allowed") == expected
        failures += not passed
        want = "allowed" if expected else "denied"
        print(f"{'PASS' if passed else 'FAIL'}  {description:48} expected {want:8} got {result}")

    print(f"\n{len(cases) - failures}/{len(cases)} as expected")
    return 1 if failures else 0


def _context(key: str, value: str) -> dict:
    return {"ContextKeyName": key, "ContextKeyValues": [value], "ContextKeyType": "string"}


if __name__ == "__main__":
    sys.exit(main())
