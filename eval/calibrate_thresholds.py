"""Calibrate the chunk scanner's thresholds on the labeled dataset, prioritizing attack recall.

    uv run python -m eval.dump_scores            # once, needs the Decider server
    uv run python -m eval.calibrate_thresholds   # offline, reads eval/results/dataset_scores.json

Reads the per-row probabilities dumped by eval.dump_scores and, without calling Decider, replays
``ScanPolicy`` at many thresholds. Reports recall (attacks caught) and the false-positive rate
(benign flagged) for the current policy, for a recommended one, and across a sweep of the
``instructs_ai_alone`` threshold at both the current and the recommended secondary bar. Writes
eval/results/calibration.{json,md} and, if matplotlib is installed, the calibration chart
eval/results/calibration.png for the article.

Only rows the chunk scanner is scored on count: expected_action ``allow`` (benign) or ``flag_chunk``
(attack). Fragmentation (``block_tool_call``) is the tool guard's job and is excluded, as in the
metrics. The numbers are in-sample on 114 rows written by the team, so they justify a threshold
choice, not a held-out estimate; the written justification is in docs/calibration.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

RESULTS = Path(__file__).parent / "results"
SCORES = RESULTS / "dataset_scores.json"
SCORED_ACTIONS = ("allow", "flag_chunk")


@dataclass(frozen=True)
class PolicyParams:
    """The ScanPolicy thresholds, replayed here so the sweep needs no Decider."""

    instructs_ai: float = 0.5
    instructs_ai_unseen: float = 0.35
    instructs_ai_alone: float = 0.9
    requests_user_data: float = 0.5
    claims_authority: float = 0.5


CURRENT = PolicyParams()
# Chosen on dataset_scores.json and checked out of sample; the reasoning is in
# docs/calibration.md. The "alone" rule drops to 0.82 (not 0.80: out of sample a dev comment
# scores 0.807, so 0.82 sits between the highest benign seen, 0.807, and the next attack, 0.843),
# and the data bar to 0.40 (which added no false alarm out of sample). The other thresholds stay,
# because moving them would chase single rows.
RECOMMENDED = PolicyParams(instructs_ai_alone=0.82, requests_user_data=0.40)


def flags(probs: dict[str, float], visibility: str, p: PolicyParams) -> bool:
    """ScanPolicy.flags, replayed from stored probabilities (guardrails/scanner.py)."""
    if probs["instructs_ai"] >= p.instructs_ai_alone:
        return True
    bar = p.instructs_ai if visibility == "visible" else p.instructs_ai_unseen
    return probs["instructs_ai"] >= bar and (
        probs["requests_user_data"] >= p.requests_user_data
        or probs["claims_authority"] >= p.claims_authority
    )


def scored_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if r["expected_action"] in SCORED_ACTIONS]


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 3) if den else None


def evaluate(rows: list[dict[str, Any]], p: PolicyParams) -> dict[str, Any]:
    """Recall, false-positive rate and the missed / false-alarm rows for one threshold set."""
    scored = scored_rows(rows)
    tp = fn = fp = tn = 0
    missed: list[dict[str, Any]] = []
    false_alarms: list[dict[str, Any]] = []
    for r in scored:
        flagged = flags(r["probabilities"], r["visibility"], p)
        if r["is_attack"]:
            if flagged:
                tp += 1
            else:
                fn += 1
                missed.append(r)
        elif flagged:
            fp += 1
            false_alarms.append(r)
        else:
            tn += 1

    def brief(r: dict[str, Any]) -> dict[str, Any]:
        probs = {k: round(v, 3) for k, v in r["probabilities"].items()}
        return {
            "technique": r["technique"],
            "visibility": r["visibility"],
            "shown": r["shown"][:110],
            "probabilities": probs,
        }

    return {
        "params": vars(p),
        "tp": tp,
        "fn": fn,
        "fp": fp,
        "tn": tn,
        "recall": _ratio(tp, tp + fn),
        "false_positive_rate": _ratio(fp, fp + tn),
        "precision": _ratio(tp, tp + fp),
        "missed": [brief(r) for r in missed],
        "false_alarms": [brief(r) for r in false_alarms],
    }


def _grid(lo: float, hi: float, step: float) -> list[float]:
    n = round((hi - lo) / step)
    return [round(lo + i * step, 3) for i in range(n + 1)]


def sweep(
    rows: list[dict[str, Any]], field: str, base: PolicyParams, values: list[float]
) -> list[dict[str, Any]]:
    """Vary one threshold, holding the rest at ``base``; recall and FPR at each value."""
    out = []
    for v in values:
        e = evaluate(rows, replace(base, **{field: v}))
        out.append(
            {
                "value": v,
                "recall": e["recall"],
                "false_positive_rate": e["false_positive_rate"],
                "tp": e["tp"],
                "fn": e["fn"],
                "fp": e["fp"],
                "tn": e["tn"],
            }
        )
    return out


def lowest_at_fpr(points: list[dict[str, Any]], fpr_max: float) -> dict[str, Any] | None:
    """The sweep point with the best recall whose FPR stays within ``fpr_max`` (lowest value wins
    ties, i.e. the most recall for the budget)."""
    ok = [pt for pt in points if (pt["false_positive_rate"] or 0.0) <= fpr_max]
    return max(ok, key=lambda pt: (pt["recall"] or 0.0, -pt["value"])) if ok else None


def _distribution(rows: list[dict[str, Any]], field: str) -> dict[str, list[float]]:
    scored = scored_rows(rows)
    return {
        "attack": sorted(round(r["probabilities"][field], 3) for r in scored if r["is_attack"]),
        "benign": sorted(round(r["probabilities"][field], 3) for r in scored if not r["is_attack"]),
    }


def analyze(rows: list[dict[str, Any]]) -> dict[str, Any]:
    alone = _grid(0.5, 1.0, 0.01)
    rec_secondary = replace(
        CURRENT,
        requests_user_data=RECOMMENDED.requests_user_data,
        claims_authority=RECOMMENDED.claims_authority,
    )
    return {
        "current": evaluate(rows, CURRENT),
        "recommended": evaluate(rows, RECOMMENDED),
        "sweeps": {
            "alone_current_secondary": sweep(rows, "instructs_ai_alone", CURRENT, alone),
            "alone_rec_secondary": sweep(rows, "instructs_ai_alone", rec_secondary, alone),
        },
        "instructs_ai_distribution": _distribution(rows, "instructs_ai"),
    }


def _policy_row(name: str, e: dict[str, Any]) -> str:
    return (
        f"| {name} | {e['recall']} | {e['false_positive_rate']} | {e['precision']} | "
        f"{e['tp']} | {e['fn']} | {e['fp']} | {e['tn']} |"
    )


def _params(e: dict[str, Any]) -> str:
    p = e["params"]
    return (
        f"instructs_ai={p['instructs_ai']}, unseen={p['instructs_ai_unseen']}, "
        f"alone={p['instructs_ai_alone']}, req={p['requests_user_data']}, "
        f"auth={p['claims_authority']}"
    )


def _median(xs: list[float]) -> float:
    n = len(xs)
    return xs[n // 2] if n % 2 else round((xs[n // 2 - 1] + xs[n // 2]) / 2, 3)


def markdown(result: dict[str, Any], decider: dict[str, Any]) -> str:
    cur, rec = result["current"], result["recommended"]
    dist = result["instructs_ai_distribution"]
    cur_sweep = {pt["value"]: pt for pt in result["sweeps"]["alone_current_secondary"]}
    rec_sweep = {pt["value"]: pt for pt in result["sweeps"]["alone_rec_secondary"]}
    where = ", ".join(f"{k} `{decider[k]}`" for k in ("device", "model") if decider.get(k))
    lines = [
        "# Threshold calibration — chunk scanner",
        "",
        "Generated by `uv run python -m eval.calibrate_thresholds` from "
        "`eval/results/dataset_scores.json`. Do not edit by hand; the written justification is in "
        "[`docs/calibration.md`](../../docs/calibration.md).",
    ]
    if where:
        lines.append(f"Decider: {where}.")
    lines += [
        "",
        "## Current vs recommended",
        "",
        "| Policy | Recall (attacks) | FPR | Precision | TP | FN | FP | TN |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
        _policy_row("current", cur),
        _policy_row("recommended", rec),
        "",
        f"- Current: `{_params(cur)}`",
        f"- Recommended: `{_params(rec)}`",
        "",
        "## `instructs_ai` distribution (scored rows)",
        "",
        f"- Benign (n={len(dist['benign'])}): min {dist['benign'][0]}, "
        f"median {_median(dist['benign'])}, max {dist['benign'][-1]}; "
        f"top three {dist['benign'][-3:]}",
        f"- Attacks (n={len(dist['attack'])}): min {dist['attack'][0]}, "
        f"median {_median(dist['attack'])}, max {dist['attack'][-1]}",
        "",
        "## Sweep: `instructs_ai_alone`",
        "",
        "Recall at the current data bar (`req` 0.50) and at the recommended one (`req` 0.40), with "
        "the false-positive rate (the same at both bars here).",
        "",
        "| alone | recall (req .50) | recall (req .40) | FPR |",
        "| --- | --- | --- | --- |",
    ]
    for v in sorted(rec_sweep, reverse=True):
        if round(v * 100) % 5 == 0:
            lines.append(
                f"| {v:.2f} | {cur_sweep[v]['recall']} | {rec_sweep[v]['recall']} | "
                f"{rec_sweep[v]['false_positive_rate']} |"
            )
    lines += ["", "## Still missed under the recommended policy", ""]
    for r in rec["missed"]:
        lines.append(f"- `{r['technique']}` ({r['visibility']}) {r['probabilities']}: {r['shown']}")
    if not rec["missed"]:
        lines.append("- none")
    lines += ["", "## False positives under the recommended policy", ""]
    for r in rec["false_alarms"]:
        lines.append(f"- `{r['technique']}` ({r['visibility']}) {r['probabilities']}: {r['shown']}")
    if not rec["false_alarms"]:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def plot(result: dict[str, Any], path: Path) -> bool:
    """Recall and false-positive rate as the ``instructs_ai_alone`` threshold moves, at both the
    current and the recommended data bar. Returns False when matplotlib is missing, so the data
    outputs still get written."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False

    cur_sweep = result["sweeps"]["alone_current_secondary"]
    rec_sweep = result["sweeps"]["alone_rec_secondary"]
    xs = [p["value"] for p in rec_sweep]
    recall_cur = [p["recall"] or 0.0 for p in cur_sweep]
    recall_rec = [p["recall"] or 0.0 for p in rec_sweep]
    fpr = [p["false_positive_rate"] or 0.0 for p in rec_sweep]

    fig, ax = plt.subplots(figsize=(7.5, 4.8))
    ax.plot(xs, recall_rec, color="#2563eb", linewidth=2, label="Recall — data bar 0.40")
    ax.plot(
        xs,
        recall_cur,
        color="#2563eb",
        linewidth=1.3,
        linestyle=":",
        alpha=0.7,
        label="Recall — data bar 0.50 (current)",
    )
    ax.plot(xs, fpr, color="#dc2626", linewidth=2, label="False-positive rate")

    cur_recall = next(p["recall"] for p in cur_sweep if p["value"] == CURRENT.instructs_ai_alone)
    rec_recall = next(
        p["recall"] for p in rec_sweep if p["value"] == RECOMMENDED.instructs_ai_alone
    )
    ax.scatter([CURRENT.instructs_ai_alone], [cur_recall], color="#6b7280", zorder=5)
    ax.annotate(
        f"current\n{cur_recall}",
        (CURRENT.instructs_ai_alone, cur_recall),
        textcoords="offset points",
        xytext=(6, -22),
        fontsize=9,
        color="#374151",
    )
    ax.scatter([RECOMMENDED.instructs_ai_alone], [rec_recall], color="#059669", zorder=5)
    ax.annotate(
        f"recommended\n{rec_recall}",
        (RECOMMENDED.instructs_ai_alone, rec_recall),
        textcoords="offset points",
        xytext=(-4, 10),
        fontsize=9,
        color="#047857",
    )

    ax.set_xlabel("instructs_ai_alone threshold (the '≥ this flags on its own' rule)")
    ax.set_ylabel("rate")
    ax.set_ylim(0, 1.02)
    ax.set_xlim(xs[-1], xs[0])  # strict → lenient, left to right
    ax.grid(True, alpha=0.25)
    ax.legend(loc="center right", fontsize=9)
    ax.set_title("Attack recall vs false positives as the scanner's thresholds loosen")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return True


def main() -> int:
    if not SCORES.exists():
        print(f"{SCORES} not found. Run `uv run python -m eval.dump_scores` first (needs Decider).")
        return 1
    data = json.loads(SCORES.read_text(encoding="utf-8"))
    rows = data["rows"]
    result = analyze(rows)
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "calibration.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
    )
    md = markdown(result, data.get("decider", {}))
    (RESULTS / "calibration.md").write_text(md, encoding="utf-8")
    drew = plot(result, RESULTS / "calibration.png")
    print(md)
    print("Wrote calibration.png" if drew else "matplotlib not installed; skipped calibration.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
