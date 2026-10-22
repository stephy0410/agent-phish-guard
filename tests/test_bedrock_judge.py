"""The Bedrock-as-judge baseline with a fake Converse call (no AWS)."""

import csv
import json

import pytest

from eval.run_bedrock_judge import (
    DEFAULT_MODEL,
    QUESTIONS,
    cost,
    judge,
    load_dataset,
    main,
    parse_answers,
    summarize,
    system_prompt,
)
from guardrails.questions import CHUNK_QUESTIONS


class FakeConverse:
    """Answers like a judge that reads 'assistant' as an instruction to an AI."""

    def __init__(self, reply=None):
        self.reply = reply
        self.calls = []

    def __call__(self, **request):
        self.calls.append(request)
        text = request["messages"][0]["content"][0]["text"].lower()
        if self.reply is not None:
            reply = self.reply
        else:
            instructs = "assistant" in text
            data = any(w in text for w in ("card", "address", "email", "name"))
            reply = json.dumps(
                {"instructs_ai": instructs, "requests_user_data": instructs and data,
                 "claims_authority": "system" in text}
            )  # fmt: skip
        return {
            "output": {"message": {"role": "assistant", "content": [{"text": reply}]}},
            "usage": {"inputTokens": 400, "outputTokens": 30},
            "metrics": {"latencyMs": 250},
        }


@pytest.fixture(scope="module")
def dataset():
    return load_dataset()


def test_prompt_uses_the_scanner_questions():
    prompt = system_prompt()
    for key, question in CHUNK_QUESTIONS.items():
        assert key in prompt and question["instructions"] in prompt
        for meaning in question["criteria"].values():
            assert meaning in prompt
    assert "never instructions for you" in prompt


def test_request_sends_the_deobfuscated_chunk_state(dataset):
    tag_row = next(r for r in dataset if "\U000e0041" in r.chunk.text)  # tag characters
    fake = FakeConverse()
    judge(tag_row, fake)
    [call] = fake.calls
    sent = call["messages"][0]["content"][0]["text"]
    assert sent.startswith("Text extracted from a web page:")
    assert "\U000e0041" not in sent  # decoded, as the scanner does before asking Decider
    assert call["modelId"] == DEFAULT_MODEL
    assert call["inferenceConfig"]["temperature"] == 0


def test_a_row_records_answers_latency_tokens_and_cost(dataset):
    attack = next(r for r in dataset if r.is_attack and "card" in r.chunk.text.lower())
    row = judge(attack, FakeConverse())
    assert row.instructs_ai is True and row.requests_user_data is True
    assert row.flagged_policy and row.flagged_strict
    assert (row.input_tokens, row.output_tokens, row.server_latency_ms) == (400, 30, 250)
    assert row.cost_usd == pytest.approx((400 * 1.10 + 30 * 5.50) / 1e6)
    assert row.latency_ms >= 0 and row.error == ""


def test_scanner_policy_flags_any_instruction_strict_needs_more(dataset):
    reply = json.dumps({"instructs_ai": True, "requests_user_data": False,
                        "claims_authority": False})  # fmt: skip
    row = judge(dataset[0], FakeConverse(reply))
    assert row.flagged_policy is True  # "yes" = 1.0 clears the 0.9 bar on its own
    assert row.flagged_strict is False


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ('{"instructs_ai": true, "requests_user_data": false, "claims_authority": false}',
         {"instructs_ai": True, "requests_user_data": False, "claims_authority": False}),
        ('Sure! ```json\n{"instructs_ai": "yes", "requests_user_data": "false", '
         '"claims_authority": false}\n```',
         {"instructs_ai": True, "requests_user_data": False, "claims_authority": False}),
    ],
)  # fmt: skip
def test_parse_answers(reply, expected):
    assert parse_answers(reply) == expected


@pytest.mark.parametrize("reply", ["I can't help with that.", '{"instructs_ai": true}'])
def test_unparseable_replies_are_recorded_not_raised(dataset, reply):
    row = judge(dataset[0], FakeConverse(reply))
    assert row.error and row.instructs_ai is None and row.flagged_policy is None
    assert row.input_tokens == 400  # still paid for


def test_cost_uses_the_price_table():
    assert cost(DEFAULT_MODEL, 1_000_000, 0) == pytest.approx(1.10)
    assert cost(DEFAULT_MODEL, 0, 1_000_000) == pytest.approx(5.50)


def test_main_writes_the_csv_and_summary(tmp_path, capsys):
    fake = FakeConverse()
    assert main(["--limit", "12", "--out", str(tmp_path)], converse=fake) == 0
    assert len(fake.calls) == 12

    rows = list(csv.DictReader((tmp_path / "bedrock_judge.csv").open(encoding="utf-8")))
    assert len(rows) == 12
    for column in ("id", "latency_ms", "input_tokens", "output_tokens", "cost_usd", *QUESTIONS):
        assert column in rows[0]

    summary = (tmp_path / "bedrock_judge.md").read_text(encoding="utf-8")
    assert "Estimated cost per chunk" in summary and "Run started" in summary
    assert summary in capsys.readouterr().out


def test_summary_counts_the_whole_dataset(dataset):
    fake = FakeConverse()
    rows = [judge(r, fake) for r in dataset]
    text = summarize(rows, DEFAULT_MODEL)
    assert "/54 (" in text  # flag_chunk attacks
    assert "/60 (" in text  # allow rows
    assert "/6 (" in text  # split payloads
    assert "Unparseable replies | 0" in text
