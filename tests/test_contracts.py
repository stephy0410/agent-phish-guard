import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts.__main__ import SCHEMA_DIR, render
from contracts.models import SCHEMAS, Action, Chunk, Decision, LabeledChunk

EXAMPLES = sorted((Path(__file__).parents[1] / "contracts" / "examples").glob("*.json"))


def model_for(example: Path):
    # action_tool_call.json is an Action, chunk.json a Chunk, and so on.
    for name in sorted(SCHEMAS, key=len, reverse=True):
        if example.stem.startswith(name):
            return SCHEMAS[name]
    raise AssertionError(f"no model for {example.name}")


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.name)
def test_examples_are_valid(example):
    model_for(example).model_validate_json(example.read_text())


def test_every_schema_has_an_example():
    assert {model_for(e) for e in EXAMPLES} == set(SCHEMAS.values())


@pytest.mark.parametrize("name", SCHEMAS)
def test_committed_schemas_match_the_models(name):
    committed = (SCHEMA_DIR / f"{name}.schema.json").read_text()
    assert committed == render(name), "run `uv run python -m contracts` and commit the result"


CHUNK = json.loads((SCHEMA_DIR.parent / "examples" / "chunk.json").read_text())
DECISION = json.loads((SCHEMA_DIR.parent / "examples" / "decision.json").read_text())


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        Chunk.model_validate({**CHUNK, "html": "<div>"})


@pytest.mark.parametrize("visibility", ["shown", "", None])
def test_visibility_must_be_known(visibility):
    with pytest.raises(ValidationError):
        Chunk.model_validate({**CHUNK, "visibility": visibility})


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_confidence_is_between_0_and_1(confidence):
    with pytest.raises(ValidationError):
        Decision.model_validate({**DECISION, "confidence": confidence})


@pytest.mark.parametrize(
    "fields",
    [
        {"type": "proceed", "message": "not allowed"},
        {"type": "guide", "message": None},
        {"type": "deny", "message": ""},
        {"type": "confirm", "message": "Send it?"},
    ],
)
def test_action_message_rules(fields):
    base = {"subject_kind": "chunk", "subject_id": "5f2a9c1e:7", "reason": "test"}
    with pytest.raises(ValidationError):
        Action.model_validate({**base, **fields})


def test_confirm_is_allowed_on_tool_calls():
    Action(
        type="confirm",
        subject_kind="tool_call",
        subject_id="tooluse_1",
        reason="sensitive tool",
        message="Send your card details to the store?",
    )


LABELED = json.loads((SCHEMA_DIR.parent / "examples" / "labeled_chunk.json").read_text())


def test_benign_look_alike_is_valid():
    LabeledChunk.model_validate(
        {**LABELED, "is_attack": False, "intent": None, "expected_action": "allow"}
    )


@pytest.mark.parametrize(
    "fields",
    [
        {"intent": None},  # attack without intent
        {"expected_action": "allow"},  # attack expected to pass
        {"is_attack": False, "expected_action": "allow"},  # benign with an intent
        {"is_attack": False, "intent": None, "expected_action": "flag_chunk"},  # benign flagged
        {"vector": "header"},  # unknown vector
        {"technique": "css-hidden"},  # not in the taxonomy's vocabulary
    ],
)
def test_labeled_chunk_rules(fields):
    with pytest.raises(ValidationError):
        LabeledChunk.model_validate({**LABELED, **fields})
