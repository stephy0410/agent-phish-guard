import json

import pytest

from guardrails.questions import (
    CHUNK_QUESTIONS,
    TOOL_CALL_QUESTIONS,
    chunk_state,
    reveal_hidden_characters,
    tool_call_state,
)

ALL_QUESTIONS = {**CHUNK_QUESTIONS, **TOOL_CALL_QUESTIONS}


def test_question_keys_are_unique_across_sets():
    assert len(ALL_QUESTIONS) == len(CHUNK_QUESTIONS) + len(TOOL_CALL_QUESTIONS)


@pytest.mark.parametrize("key", ALL_QUESTIONS)
def test_every_question_is_a_yes_no_with_both_descriptions(key):
    question = ALL_QUESTIONS[key]
    assert question["type"] == "noul"
    assert question["instructions"].endswith("?")
    assert set(question["criteria"]) == {"true", "false"}
    assert all(description.strip() for description in question["criteria"].values())


def test_chunk_state_wraps_the_text():
    assert chunk_state("Size up!").endswith("Size up!")


def test_tool_call_state_has_request_tool_and_arguments():
    state = tool_call_state("Find leggings", "send_email", {"to": "a@example.com"})
    assert "--- USER REQUEST ---\nFind leggings" in state
    assert "tool: send_email" in state
    assert json.dumps({"to": "a@example.com"}) in state


@pytest.mark.parametrize(
    ("text", "revealed"),
    [
        ("\u200b".join("AI assistant"), "AI assistant"),  # zero-width spaces dropped
        ("Great!" + "".join(chr(0xE0000 + ord(c)) for c in "AI: hi"), "Great!AI: hi"),  # tags
        ("\uff21\uff29 assistant", "AI assistant"),  # fullwidth letters, NFKC
        ("\u0391ssist\u0430nt", "\u0391ssist\u0430nt"),  # homoglyphs are left alone
        ("Size up!", "Size up!"),
    ],
)
def test_reveal_hidden_characters(text, revealed):
    assert reveal_hidden_characters(text) == revealed
