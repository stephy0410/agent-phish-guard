import json

import pytest

from eval.run_questions import (
    EXAMPLES,
    VARIANTS,
    bare,
    cell,
    chunk_rule,
    is_correct,
    summarize,
)
from guardrails.questions import CHUNK_QUESTIONS, TOOL_CALL_QUESTIONS

EXAMPLE_SET = json.loads(EXAMPLES.read_text())


@pytest.mark.parametrize(
    ("section", "questions"),
    [("chunks", CHUNK_QUESTIONS), ("tool_calls", TOOL_CALL_QUESTIONS)],
)
def test_examples_answer_every_question(section, questions):
    examples = EXAMPLE_SET[section]
    assert len({ex["id"] for ex in examples}) == len(examples), "ids must be unique"
    for ex in examples:
        assert set(ex["expected"]) == set(questions), ex["id"]
        assert all(v in (True, False, None) for v in ex["expected"].values()), ex["id"]


def test_chunk_examples_cover_attacks_and_benign_look_alikes():
    flags = [ex["is_attack"] for ex in EXAMPLE_SET["chunks"]]
    assert any(flags) and not all(flags)


def test_bare_drops_only_the_descriptions():
    stripped = bare(CHUNK_QUESTIONS)
    for key, question in CHUNK_QUESTIONS.items():
        assert stripped[key] == {"type": "noul", "instructions": question["instructions"]}


def row(p_true, expected, question="instructs_ai", variant=VARIANTS[0], example_id="x"):
    return {
        "kind": "chunk",
        "id": example_id,
        "variant": variant,
        "question": question,
        "expected": expected,
        "p_true": p_true,
        "latency_ms": 100.0,
    }


@pytest.mark.parametrize(
    ("p_true", "expected", "correct"),
    [
        (0.9, True, True),
        (0.5, True, True),
        (0.49, True, False),
        (0.2, False, True),
        (0.7, None, None),
    ],
)
def test_is_correct_uses_the_cutoff_and_skips_ambiguous(p_true, expected, correct):
    assert is_correct(row(p_true, expected)) is correct


def test_cell_marks_wrong_and_unscored():
    assert cell(row(0.9, True)) == "0.90"
    assert cell(row(0.4, True)) == "0.40 ✗"
    assert cell(row(0.4, None)) == "0.40 (n/s)"


def test_summarize_ignores_ambiguous_rows():
    rows = [row(0.9, True), row(0.6, True), row(0.3, False), row(0.8, None)]
    [summary] = summarize(rows)
    assert summary["scored"] == 3
    assert summary["correct"] == 3
    assert summary["mean_p_when_true"] == 0.75
    assert summary["mean_p_when_false"] == 0.3
    assert summary["gap"] == 0.45


def test_chunk_rule_needs_instructions_plus_data_or_authority():
    examples = {
        "chunks": [
            {"id": "attack", "is_attack": True},
            {"id": "form", "is_attack": False},
        ]
    }
    p = {
        ("attack", "instructs_ai"): 0.8,
        ("attack", "requests_user_data"): 0.1,
        ("attack", "claims_authority"): 0.9,
        # A checkout form asks for data but does not instruct an AI: not flagged.
        ("form", "instructs_ai"): 0.2,
        ("form", "requests_user_data"): 0.95,
        ("form", "claims_authority"): 0.1,
    }
    rows = [
        row(p_true, None, question=q, variant=v, example_id=i)
        for (i, q), p_true in p.items()
        for v in VARIANTS
    ]
    for result in chunk_rule(examples, rows):
        assert result["caught"] == 1 and result["missed"] == []
        assert result["false_alarms"] == []
