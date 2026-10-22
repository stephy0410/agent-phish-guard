"""Dump the chunk scanner's per-row Decider probabilities over the evaluation dataset.

    strands-decider serve StrandsAgents/strands-decider-2B-qwen3.5-v1-2610 --port 8099
    uv run python -m eval.dump_scores

Runs the real ChunkScanner over every row of eval/data/eval_dataset.csv and records, per row,
the Decider probabilities (full precision) with the labels calibration needs: is_attack,
technique, visibility and expected_action. Writes eval/results/dataset_scores.json.

The scan is the slow, machine-bound step. eval/calibrate_thresholds.py reads this file and sweeps
thresholds offline, so the policy can be re-calibrated without the Decider server. Re-run this only
when the dataset or the Decider checkpoint changes.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from eval.build_dataset_jsonl import rows_from_csv

RESULTS = Path(__file__).parent / "results"


def score_rows(scanner: Any) -> list[dict[str, Any]]:
    """Scan every dataset row and record its probabilities and labels."""
    from contracts import Chunk
    from guardrails.deobfuscate import deobfuscate

    rows: list[dict[str, Any]] = []
    for row in rows_from_csv():
        chunk = Chunk(**row["chunk"])
        before = len(scanner.records)
        scanner.scan(chunk)
        action = scanner.records[before].action
        rows.append(
            {
                "index": row["chunk"]["index"],
                "technique": row["technique"],
                "visibility": row["chunk"]["visibility"],
                "is_attack": bool(row["is_attack"]),
                "expected_action": str(row["expected_action"]),
                "shown": deobfuscate(chunk.text),
                "probabilities": {d.question: d.probabilities["true"] for d in action.decisions},
            }
        )
    return rows


def main() -> int:
    from guardrails import decider, scanner

    sc = scanner.ChunkScanner(client=decider.DeciderClient())
    try:
        rows = score_rows(sc)
    except decider.DeciderUnavailable as exc:
        print(f"Decider is not reachable ({exc}). Start it first:")
        print(
            "  strands-decider serve StrandsAgents/strands-decider-2B-qwen3.5-v1-2610 --port 8099"
        )
        return 1
    try:
        health = sc.client.health()
    except decider.DeciderUnavailable:
        health = {}
    out = {
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "decider": health,
        "policy_at_dump": repr(sc.policy),
        "rows": rows,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / "dataset_scores.json"
    path.write_text(json.dumps(out, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"Wrote {len(rows)} rows to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
