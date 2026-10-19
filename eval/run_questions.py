"""Ask the draft typed questions about the dev examples and write the results.

    strands-decider serve StrandsAgents/strands-decider-2B-qwen3.5-v1-2610 --port 8099
    uv run python -m eval.run_questions

Every example is asked twice, everything else equal: with the true/false descriptions from
guardrails/questions.py and without them. Decider is deterministic, so one pass per variant is
enough. Writes eval/results/questions.json and eval/results/questions.md.

"Correct" uses a naive 0.5 cut-off on P(true). It is a reference point for wording, not the
guardrail's threshold, which is calibrated on the labeled dataset.
"""

from __future__ import annotations

import json
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from guardrails.decider import DeciderClient
from guardrails.questions import CHUNK_QUESTIONS, TOOL_CALL_QUESTIONS, chunk_state, tool_call_state

HERE = Path(__file__).parent
EXAMPLES = HERE / "question_examples.json"
RESULTS = HERE / "results"
CUTOFF = 0.5
VARIANTS = ("with descriptions", "without descriptions")


def bare(questions: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The same questions without their true/false descriptions."""
    return {
        key: {"type": q["type"], "instructions": q["instructions"]} for key, q in questions.items()
    }


def asked_questions() -> dict[str, dict[str, Any]]:
    """The exact questions each variant sends, recorded next to the results they produce."""
    return {
        kind: dict(zip(VARIANTS, (questions, bare(questions)), strict=True))
        for kind, questions in (("chunk", CHUNK_QUESTIONS), ("tool_call", TOOL_CALL_QUESTIONS))
    }


def ask_all(client: DeciderClient, examples: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per example, variant and question."""
    jobs = [
        ("chunk", ex, chunk_state(ex["text"]), CHUNK_QUESTIONS) for ex in examples["chunks"]
    ] + [
        (
            "tool_call",
            ex,
            tool_call_state(ex["request"], ex["tool"], ex["arguments"]),
            TOOL_CALL_QUESTIONS,
        )
        for ex in examples["tool_calls"]
    ]
    rows = []
    for kind, example, state, questions in jobs:
        for variant, asked in zip(VARIANTS, (questions, bare(questions)), strict=True):
            response = client.ask(state, asked)
            for key in questions:
                rows.append(
                    {
                        "kind": kind,
                        "id": example["id"],
                        "variant": variant,
                        "question": key,
                        "expected": example["expected"][key],
                        # Full precision: rounding before the cut-off could flip an answer.
                        "p_true": response.answers[key]["noul"],
                        "latency_ms": response.latency_ms,
                    }
                )
    return rows


def is_correct(row: dict[str, Any]) -> bool | None:
    if row["expected"] is None:
        return None
    return (row["p_true"] >= CUTOFF) == row["expected"]


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per question and variant: accuracy at the cut-off and the gap between the classes."""
    summary = []
    keys = dict.fromkeys((r["kind"], r["question"], r["variant"]) for r in rows)
    for kind, question, variant in keys:
        scored = [
            r
            for r in rows
            if (r["kind"], r["question"], r["variant"]) == (kind, question, variant)
            and r["expected"] is not None
        ]
        when_true = [r["p_true"] for r in scored if r["expected"]]
        when_false = [r["p_true"] for r in scored if not r["expected"]]
        mean_true = statistics.fmean(when_true) if when_true else None
        mean_false = statistics.fmean(when_false) if when_false else None
        summary.append(
            {
                "kind": kind,
                "question": question,
                "variant": variant,
                "scored": len(scored),
                "correct": sum(bool(is_correct(r)) for r in scored),
                "mean_p_when_true": round(mean_true, 3) if mean_true is not None else None,
                "mean_p_when_false": round(mean_false, 3) if mean_false is not None else None,
                "gap": (
                    round(mean_true - mean_false, 3)
                    if mean_true is not None and mean_false is not None
                    else None
                ),
            }
        )
    return summary


def chunk_rule(examples: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """An illustrative chunk-level rule, the decision the scanner actually makes.

    Flag a chunk when it instructs an AI and also asks for data or claims authority, each at
    the naive cut-off. The real policy and thresholds come from calibration.
    """
    p = {(r["id"], r["variant"], r["question"]): r["p_true"] for r in rows if r["kind"] == "chunk"}
    results = []
    for variant in VARIANTS:

        def yes(example_id: str, question: str, variant: str = variant) -> bool:
            return p[(example_id, variant, question)] >= CUTOFF

        missed, false_alarms = [], []
        for ex in examples["chunks"]:
            flagged = yes(ex["id"], "instructs_ai") and (
                yes(ex["id"], "requests_user_data") or yes(ex["id"], "claims_authority")
            )
            if ex["is_attack"] and not flagged:
                missed.append(ex["id"])
            elif not ex["is_attack"] and flagged:
                false_alarms.append(ex["id"])
        attacks = sum(ex["is_attack"] for ex in examples["chunks"])
        results.append(
            {
                "variant": variant,
                "attacks": attacks,
                "caught": attacks - len(missed),
                "benign": len(examples["chunks"]) - attacks,
                "missed": missed,
                "false_alarms": false_alarms,
            }
        )
    return results


def cell(row: dict[str, Any]) -> str:
    """P(true), marked ✗ when wrong at the cut-off and (n/s) when not scored."""
    correct = is_correct(row)
    mark = " (n/s)" if correct is None else ("" if correct else " ✗")
    return f"{row['p_true']:.2f}{mark}"


def _fmt(value: float | None) -> str:
    return "–" if value is None else f"{value:.2f}"


def markdown(
    examples: dict[str, Any],
    rows: list[dict[str, Any]],
    summary: list[dict[str, Any]],
    rule: list[dict[str, Any]],
) -> str:
    by_key = {(r["kind"], r["id"], r["variant"], r["question"]): r for r in rows}
    lines = [
        "# Typed questions: dev-set results",
        "",
        "Generated by `uv run python -m eval.run_questions` from "
        "[`eval/question_examples.json`](../question_examples.json). Do not edit by hand.",
        "",
        f"P(true) per question. ✗ = wrong at a naive {CUTOFF} cut-off; (n/s) = ambiguous, "
        "not scored.",
        "",
        "## Summary",
        "",
        "| Kind | Question | Variant | Correct | Mean P(true) when true | "
        "Mean P(true) when false | Gap |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for s in summary:
        lines.append(
            f"| {s['kind']} | `{s['question']}` | {s['variant']} | "
            f"{s['correct']}/{s['scored']} | {_fmt(s['mean_p_when_true'])} | "
            f"{_fmt(s['mean_p_when_false'])} | {_fmt(s['gap'])} |"
        )

    lines += [
        "",
        "## Chunk-level rule (illustrative)",
        "",
        f"Flag a chunk when `instructs_ai` ≥ {CUTOFF} and (`requests_user_data` ≥ {CUTOFF} or "
        f"`claims_authority` ≥ {CUTOFF}).",
        "",
        "| Variant | Attacks caught | False alarms on benign | Missed | False alarms |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in rule:
        lines.append(
            f"| {r['variant']} | {r['caught']}/{r['attacks']} | "
            f"{len(r['false_alarms'])}/{r['benign']} | "
            f"{', '.join(f'`{i}`' for i in r['missed']) or '–'} | "
            f"{', '.join(f'`{i}`' for i in r['false_alarms']) or '–'} |"
        )

    for kind, section, questions, label in [
        ("chunk", "chunks", CHUNK_QUESTIONS, "is_attack"),
        ("tool_call", "tool_calls", TOOL_CALL_QUESTIONS, "label"),
    ]:
        lines += [
            "",
            f"## {'Chunks' if kind == 'chunk' else 'Tool calls'}",
            "",
            "With descriptions, and without them in brackets.",
            "",
            "| Example | "
            + ("Attack" if kind == "chunk" else "Label")
            + " | "
            + " | ".join(f"`{q}`" for q in questions)
            + " |",
            "| --- | --- | " + " | ".join("---" for _ in questions) + " |",
        ]
        for ex in examples[section]:
            cells = []
            for q in questions:
                with_d = by_key[(kind, ex["id"], VARIANTS[0], q)]
                without = by_key[(kind, ex["id"], VARIANTS[1], q)]
                cells.append(f"{cell(with_d)} [{cell(without)}]")
            truth = ("yes" if ex[label] else "no") if kind == "chunk" else ex[label]
            lines.append(f"| `{ex['id']}` | {truth} | " + " | ".join(cells) + " |")

    # One latency per request: every question of an example and variant shares a request.
    per_request = {
        (r["kind"], r["id"], r["variant"]): r["latency_ms"]
        for r in rows
        if r["latency_ms"] is not None
    }
    for kind in ("chunk", "tool_call"):
        latencies = [ms for (k, _, _), ms in per_request.items() if k == kind]
        if latencies:
            lines += [
                "",
                f"Median server latency per {kind.replace('_', ' ')} request: "
                f"{statistics.median(latencies):.0f} ms over {len(latencies)} requests.",
            ]
    return "\n".join(lines) + "\n"


def main() -> int:
    examples = json.loads(EXAMPLES.read_text())
    client = DeciderClient()
    server = client.health()
    for text in ("warm-up one", "a slightly longer warm-up request for the server"):
        client.ask(chunk_state(text), CHUNK_QUESTIONS)

    rows = ask_all(client, examples)
    summary = summarize(rows)
    rule = chunk_rule(examples, rows)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "questions.json").write_text(
        json.dumps(
            {
                "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "model": server.get("model"),
                "device": server.get("device"),
                "cutoff": CUTOFF,
                "questions": asked_questions(),
                "summary": summary,
                "chunk_rule": rule,
                "rows": rows,
            },
            indent=2,
        )
        + "\n"
    )
    report = markdown(examples, rows, summary, rule)
    (RESULTS / "questions.md").write_text(report)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
