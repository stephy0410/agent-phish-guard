"""First metrics run: precision, recall and false-positive rate of the chunk scanner over the
evaluation dataset, at the current thresholds.

    strands-decider serve StrandsAgents/strands-decider-2B-qwen3.5-v1-2610 --port 8099
    uv run python -m eval.run_dataset_metrics            # needs the Decider server

Runs the real ChunkScanner (guardrails/scanner.py) over every row of eval/data/eval_dataset.csv
and compares its flag with the row's expected_action:

- allow (benign): a flag is a false positive.
- flag_chunk (attack): not flagging is a false negative.
- block_tool_call (fragmentation): the tool guard's job, not the chunk scanner's — reported
  apart and left out of precision/recall.

Writes eval/results/dataset_metrics.{json,md}: the metrics, the false positives and false
negatives to review (shown the way the scanner judged them, deobfuscated, so zero-width and
homoglyph rows are readable), and the Decider device and checkpoint from /health. The JSON keeps
full-precision probabilities; the MD rounds them. Decider is deterministic on one machine, so a
re-run on the same machine reproduces the numbers; the low-order decimals shift between machines,
so a row sitting right on a threshold can flip. The metric math (metrics, errors) is pure and
unit-tested in tests/test_run_dataset_metrics.py.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from eval.build_dataset_jsonl import rows_from_csv

RESULTS = Path(__file__).parent / "results"
SCORED = ("allow", "flag_chunk")


@dataclass
class RowResult:
    """One dataset row after the scanner saw it."""

    technique: str
    is_attack: bool
    expected_action: str
    text: str
    shown: str
    """The deobfuscated text the scanner actually judged: readable and ASCII, unlike `text`."""
    flagged: bool
    probabilities: dict[str, float]
    """Full precision; rounded only when rendered to Markdown."""

    @property
    def scored(self) -> bool:
        return self.expected_action in SCORED


def score_rows(scanner: Any) -> list[RowResult]:
    """Run the scanner over every dataset row and record what it decided."""
    # Lazy, so the metric math (metrics, errors, markdown) imports without the Decider stack.
    from contracts import Chunk
    from guardrails.deobfuscate import deobfuscate

    results: list[RowResult] = []
    for row in rows_from_csv():
        chunk = Chunk(**row["chunk"])
        before = len(scanner.records)
        scanner.scan(chunk)
        action = scanner.records[before].action
        results.append(
            RowResult(
                technique=row["technique"],
                is_attack=bool(row["is_attack"]),
                expected_action=str(row["expected_action"]),
                text=chunk.text,
                shown=deobfuscate(chunk.text),
                flagged=action.type == "deny",
                probabilities={d.question: d.probabilities["true"] for d in action.decisions},
            )
        )
    return results


def _counts(rows: list[RowResult]) -> dict[str, int]:
    return {
        "tp": sum(r.is_attack and r.flagged for r in rows),
        "fn": sum(r.is_attack and not r.flagged for r in rows),
        "fp": sum(not r.is_attack and r.flagged for r in rows),
        "tn": sum(not r.is_attack and not r.flagged for r in rows),
    }


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 3) if den else None


def metrics(results: list[RowResult]) -> dict[str, Any]:
    """Precision, recall and false-positive rate, overall and per technique."""
    scored = [r for r in results if r.scored]
    c = _counts(scored)
    overall = {
        **c,
        "precision": _ratio(c["tp"], c["tp"] + c["fp"]),
        "recall": _ratio(c["tp"], c["tp"] + c["fn"]),
        "false_positive_rate": _ratio(c["fp"], c["fp"] + c["tn"]),
    }
    per_technique = {}
    for tech in sorted({r.technique for r in scored}):
        tc = _counts([r for r in scored if r.technique == tech])
        per_technique[tech] = {
            **tc,
            "recall": _ratio(tc["tp"], tc["tp"] + tc["fn"]),
            "false_positive_rate": _ratio(tc["fp"], tc["fp"] + tc["tn"]),
        }
    frag = [r for r in results if r.expected_action == "block_tool_call"]
    return {
        "overall": overall,
        "per_technique": per_technique,
        "not_scored": {
            "block_tool_call_rows": len(frag),
            "flagged_by_chunk_scanner": sum(r.flagged for r in frag),
            "note": "fragmentation is caught at the tool-call level, not per chunk.",
        },
    }


def errors(results: list[RowResult]) -> dict[str, list[dict[str, Any]]]:
    """The rows to review: benign rows flagged, and attacks missed."""

    def shape(r: RowResult) -> dict[str, Any]:
        return {
            "technique": r.technique,
            "text": r.text,
            "shown": r.shown,
            "probabilities": r.probabilities,
        }

    scored = [r for r in results if r.scored]
    return {
        "false_positives": [shape(r) for r in scored if not r.is_attack and r.flagged],
        "false_negatives": [shape(r) for r in scored if r.is_attack and not r.flagged],
    }


def _error_lines(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["- none"]
    lines = []
    for r in rows:
        probs = {k: round(v, 3) for k, v in r["probabilities"].items()}
        lines.append(f"- `{r['technique']}` {probs}: {r['shown'][:120]}")
    return lines


def markdown(result: dict[str, Any]) -> str:
    m, e = result["metrics"], result["errors"]
    o, ns = m["overall"], m["not_scored"]
    dec = result.get("decider") or {}
    lines = [
        "# Dataset metrics — chunk scanner",
        "",
        f"Generated {result['generated_at']} with `eval/run_dataset_metrics.py` over "
        "`eval/data/eval_dataset.csv`.",
    ]
    if dec:
        lines.append("Decider /health: " + ", ".join(f"{k} `{v}`" for k, v in dec.items()) + ".")
    lines += [
        f"Policy: `{result['policy']}`. Scored on `allow` + `flag_chunk`; `block_tool_call` "
        "(fragmentation) is the tool guard's job and is reported apart.",
        "",
        "## Overall",
        "",
        f"- **Precision {o['precision']}** · **Recall {o['recall']}** · "
        f"**False-positive rate {o['false_positive_rate']}**",
        f"- TP {o['tp']} · FN {o['fn']} · FP {o['fp']} · TN {o['tn']}",
        f"- Fragmentation (not scored): {ns['flagged_by_chunk_scanner']}/"
        f"{ns['block_tool_call_rows']} flagged per chunk",
        "",
        "## Per technique",
        "",
        "| Technique | Recall (attacks caught) | False positives (benign flagged) |",
        "| --- | --- | --- |",
    ]
    for tech, t in m["per_technique"].items():
        caught = f"{t['tp']}/{t['tp'] + t['fn']}" if t["tp"] + t["fn"] else "—"
        fp = f"{t['fp']}/{t['fp'] + t['tn']}" if t["fp"] + t["tn"] else "—"
        lines.append(f"| `{tech}` | {caught} | {fp} |")
    lines += ["", "## Errors to review", ""]
    lines.append("Text is shown deobfuscated (what the scanner judged). Probabilities rounded.")
    lines += ["", "### False positives (benign flagged)", "", *_error_lines(e["false_positives"])]
    lines += ["", "### False negatives (attacks missed)", "", *_error_lines(e["false_negatives"])]
    return "\n".join(lines) + "\n"


def main() -> int:
    from guardrails import decider, scanner

    chunk_scanner = scanner.ChunkScanner(client=decider.DeciderClient())
    try:
        results = score_rows(chunk_scanner)
    except decider.DeciderUnavailable as exc:
        print(f"Decider is not reachable ({exc}). Start it first:")
        print(
            "  strands-decider serve StrandsAgents/strands-decider-2B-qwen3.5-v1-2610 --port 8099"
        )
        return 1
    try:
        decider_info = chunk_scanner.client.health()
    except decider.DeciderUnavailable:
        decider_info = {}
    result = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "decider": decider_info,
        "policy": repr(chunk_scanner.policy),
        "metrics": metrics(results),
        "errors": errors(results),
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "dataset_metrics.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
    )
    md = markdown(result)
    (RESULTS / "dataset_metrics.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
