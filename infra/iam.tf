data "aws_caller_identity" "current" {}

# Each cross-region inference profile routes to the same model in several US regions. Reading the
# profiles from AWS gives the exact foundation-model ARNs behind them, so the policy needs no
# region or model wildcards.
data "aws_bedrock_inference_profile" "agent" {
  for_each             = toset(var.bedrock_inference_profiles)
  inference_profile_id = each.value
}

locals {
  inference_profile_arns = sort([for p in data.aws_bedrock_inference_profile.agent : p.inference_profile_arn])
  foundation_model_arns = sort(distinct(flatten([
    for p in data.aws_bedrock_inference_profile.agent : [for m in p.models : m.model_arn]
  ])))
  invoke_actions = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
}

# --------------------------------------------------------------------------- invoke policy

# Converse and ConverseStream are authorized through these two actions.
data "aws_iam_policy_document" "bedrock_invoke" {
  statement {
    sid       = "InvokeAgentInferenceProfiles"
    actions   = local.invoke_actions
    resources = local.inference_profile_arns
  }

  statement {
    sid       = "InvokeModelsOnlyThroughTheProfiles"
    actions   = local.invoke_actions
    resources = local.foundation_model_arns

    condition {
      test     = "StringEquals"
      variable = "bedrock:InferenceProfileArn"
      values   = local.inference_profile_arns
    }
  }
}

resource "aws_iam_policy" "bedrock_invoke" {
  name        = "agent-phish-guard-bedrock-invoke"
  description = "Invoke only the Claude models the agent uses, only through their inference profiles."
  policy      = data.aws_iam_policy_document.bedrock_invoke.json
}

# --------------------------------------------------------------------------- agent role

data "aws_iam_policy_document" "agent_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = var.agent_trusted_services
    }
  }
}

resource "aws_iam_role" "agent" {
  name                 = "agent-phish-guard-agent"
  description          = "Runs the Agent Phish Guard agent. It can only invoke the agent's Bedrock models."
  assume_role_policy   = data.aws_iam_policy_document.agent_trust.json
  max_session_duration = 3600
}

resource "aws_iam_role_policy_attachment" "agent_bedrock_invoke" {
  role       = aws_iam_role.agent.name
  policy_arn = aws_iam_policy.bedrock_invoke.arn
}

# Lets an EC2 instance take the role. Free, and harmless if the agent never runs on EC2.
resource "aws_iam_instance_profile" "agent" {
  name = "agent-phish-guard-agent"
  role = aws_iam_role.agent.name
}

# --------------------------------------------------------------------------- developers

# Teammates get the same invoke permission plus read-only model listing and what they need to
# manage their own password and MFA. No access keys: they sign in with `aws login`.
data "aws_iam_policy_document" "developer_self_service" {
  statement {
    sid = "ListBedrockModels"
    actions = [
      "bedrock:GetFoundationModel",
      "bedrock:GetInferenceProfile",
      "bedrock:ListFoundationModels",
      "bedrock:ListInferenceProfiles",
    ]
    resources = ["*"]
  }

  statement {
    sid = "ManageOwnPasswordAndMfa"
    actions = [
      "iam:ChangePassword",
      "iam:GetUser",
      "iam:GetLoginProfile",
      "iam:ListMFADevices",
      "iam:EnableMFADevice",
      "iam:ResyncMFADevice",
    ]
    resources = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:user/phishguard/$${aws:username}"]
  }

  statement {
    sid       = "CreateOwnVirtualMfaDevice"
    actions   = ["iam:CreateVirtualMFADevice"]
    resources = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:mfa/*"]
  }

  statement {
    sid       = "ReadAccountPasswordPolicy"
    actions   = ["iam:GetAccountPasswordPolicy", "iam:ListVirtualMFADevices"]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "developer_self_service" {
  name        = "agent-phish-guard-developer-self-service"
  description = "List Bedrock models and manage your own password and MFA."
  policy      = data.aws_iam_policy_document.developer_self_service.json
}

resource "aws_iam_group" "developers" {
  name = "phishguard-developers"
  path = "/phishguard/"
}

resource "aws_iam_group_policy_attachment" "developers_bedrock_invoke" {
  group      = aws_iam_group.developers.name
  policy_arn = aws_iam_policy.bedrock_invoke.arn
}

resource "aws_iam_group_policy_attachment" "developers_self_service" {
  group      = aws_iam_group.developers.name
  policy_arn = aws_iam_policy.developer_self_service.arn
}

# `aws login` turns a console session into CLI credentials through AWS Sign-in's OAuth2 flow,
# which needs signin:AuthorizeOAuth2Access and signin:CreateOAuth2Token. This AWS managed
# policy grants exactly those, for public OAuth2 clients only.
resource "aws_iam_group_policy_attachment" "developers_cli_sign_in" {
  group      = aws_iam_group.developers.name
  policy_arn = "arn:aws:iam::aws:policy/SignInLocalDevelopmentAccess"
}

resource "aws_iam_user" "developer" {
  for_each = var.developers
  name     = each.value
  path     = "/phishguard/"

  # Lets `terraform destroy` remove users who added a console password or MFA by hand.
  force_destroy = true
}

resource "aws_iam_group_membership" "developers" {
  name  = "phishguard-developers"
  group = aws_iam_group.developers.name
  users = [for u in aws_iam_user.developer : u.name]
}
