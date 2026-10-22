"""Latency and cost per chunk: Decider 2B against Bedrock as judge, on the evaluation dataset.

    strands-decider serve StrandsAgents/strands-decider-2B-qwen3.5-v1-2610 --port 8099
    uv run python -m eval.bench_vs_bedrock                            # measure, write the table
    uv run --with matplotlib python -m eval.bench_vs_bedrock --plot   # and draw the chart
    uv run --with matplotlib python -m eval.bench_vs_bedrock --plot-only   # redraw, no Decider

Decider gets the chunks of eval/data/eval_dataset.jsonl exactly as the scanner sends them
(deobfuscated, the three chunk questions in one request), which is also the input
eval/run_bedrock_judge.py gave Haiku. Bedrock's numbers are read from
eval/results/bedrock_judge.csv, not measured again, and both sides go through the same
percentile function.

The chunks are sent twice. On Apple silicon the first request at each new input length compiles
kernels, so the first pass is what a fresh session sees and the second is the steady state.

Writes eval/results/latency_cost.{csv,json,md} and, with --plot, docs/img/latency_cost.png. The plot
needs matplotlib, which is not a project dependency: ``uv run --with matplotlib`` brings it for
that one run.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from eval.bench_decider_latency import hardware
from eval.run_bedrock_judge import DATASET, DEFAULT_MODEL, load_dataset

HERE = Path(__file__).parent
RESULTS = HERE / "results"
BEDROCK_CSV = RESULTS / "bedrock_judge.csv"
CHART = HERE.parent / "docs" / "img" / "latency_cost.png"
EXAMPLE_INSTANCE = ("m7i.xlarge", 0.2016)
"""An x86 instance with 16 GiB, on-demand in us-east-1, USD per hour. Only used to illustrate
the break-even point; Decider's speed on it was not measured."""


@dataclass
class DeciderRow:
    id: str
    technique: str
    run: int
    round_trip_ms: float
    server_ms: float | None
    input_tokens: int


def percentile(values: list[float], q: float) -> float:
    """The q-th percentile (0-100), linear between closest ranks, as numpy's default."""
    data = sorted(values)
    if not data:
        raise ValueError("no values")
    position = (len(data) - 1) * q / 100
    low = int(position)
    high = min(low + 1, len(data) - 1)
    return data[low] + (data[high] - data[low]) * (position - low)


def p50_p95(values: list[float]) -> tuple[float, float]:
    return percentile(values, 50), percentile(values, 95)


def measure_decider(client: Any, records: list[Any], runs: int = 2) -> list[DeciderRow]:
    """Ask Decider about every chunk, ``runs`` times over, the way the scanner does."""
    from guardrails.deobfuscate import deobfuscate
    from guardrails.questions import CHUNK_QUESTIONS, chunk_state

    rows = []
    for run in range(1, runs + 1):
        for record in records:
            state = chunk_state(deobfuscate(record.chunk.text))
            start = time.perf_counter()
            response = client.ask(state, CHUNK_QUESTIONS)
            rows.append(
                DeciderRow(
                    id=record.chunk.id,
                    technique=record.technique,
                    run=run,
                    round_trip_ms=round((time.perf_counter() - start) * 1000, 1),
                    server_ms=response.latency_ms,
                    input_tokens=response.input_tokens,
                )
            )
        print(f"pass {run}/{runs} done", file=sys.stderr)
    return rows


@dataclass(frozen=True)
class BedrockRun:
    round_trip_ms: list[float]
    server_ms: list[float]
    cost_per_chunk: float
    input_tokens: float
    output_tokens: float
    model: str


def read_bedrock(path: Path = BEDROCK_CSV, model: str = "") -> BedrockRun:
    with path.open(encoding="utf-8", newline="") as f:
        rows = [r for r in csv.DictReader(f) if not r["error"]]
    return BedrockRun(
        round_trip_ms=[float(r["latency_ms"]) for r in rows],
        server_ms=[float(r["server_latency_ms"]) for r in rows if r["server_latency_ms"]],
        cost_per_chunk=statistics.fmean(float(r["cost_usd"]) for r in rows),
        input_tokens=statistics.fmean(int(r["input_tokens"]) for r in rows),
        output_tokens=statistics.fmean(int(r["output_tokens"]) for r in rows),
        model=model,
    )


def break_even_chunks_per_hour(hourly_usd: float, judge_cost_per_chunk: float) -> float:
    """Chunks per hour above which a machine rented by the hour costs less than the judge."""
    return hourly_usd / judge_cost_per_chunk


def summary(decider: list[DeciderRow], bedrock: BedrockRun, server: dict, machine: dict) -> dict:
    last = max(r.run for r in decider)
    warm = [r for r in decider if r.run == last]
    cold = [r for r in decider if r.run == 1]
    warm_ms = [r.round_trip_ms for r in warm]
    seconds_per_chunk = statistics.fmean(warm_ms) / 1000
    instance, price = EXAMPLE_INSTANCE
    break_even = break_even_chunks_per_hour(price, bedrock.cost_per_chunk)
    return {
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "chunks": len(warm),
        "decider": {
            "device": server.get("device"),
            "checkpoint": server.get("checkpoint"),
            "machine": machine,
            "warm_round_trip_ms": p50_p95(warm_ms),
            "warm_server_ms": p50_p95([r.server_ms for r in warm if r.server_ms is not None]),
            "cold_round_trip_ms": p50_p95([r.round_trip_ms for r in cold]),
            "input_tokens": statistics.fmean(r.input_tokens for r in warm),
            "cost_per_1000_usd": 0.0,
            "machine_minutes_per_1000": seconds_per_chunk * 1000 / 60,
        },
        "bedrock": {
            "model": bedrock.model,
            "round_trip_ms": p50_p95(bedrock.round_trip_ms),
            "server_ms": p50_p95(bedrock.server_ms),
            "input_tokens": bedrock.input_tokens,
            "output_tokens": bedrock.output_tokens,
            "cost_per_1000_usd": bedrock.cost_per_chunk * 1000,
        },
        "break_even": {
            "instance": instance,
            "usd_per_hour": price,
            "chunks_per_hour": break_even,
            "max_seconds_per_chunk": 3600 / break_even,
        },
    }


def markdown(s: dict) -> str:
    d, b, e = s["decider"], s["bedrock"], s["break_even"]

    def ms(pair) -> str:
        return f"{pair[0]:.0f} ms / {pair[1]:.0f} ms"

    machine = d["machine"].get("cpu", "?")
    return "\n".join(
        [
            "# Latency and cost per chunk: Decider 2B and Bedrock as judge",
            "",
            "Generated by `uv run python -m eval.bench_vs_bedrock`. Do not edit by hand.",
            f"Measured {s['measured_at']}, {s['chunks']} chunks of "
            "`eval/data/eval_dataset.jsonl`, the three scanner questions per chunk.",
            "",
            "| p50 / p95 per chunk | Decider 2B | Haiku 4.5 as judge |",
            "| --- | --- | --- |",
            f"| Round trip (what the agent waits) | {ms(d['warm_round_trip_ms'])} "
            f"| {ms(b['round_trip_ms'])} |",
            f"| Server side | {ms(d['warm_server_ms'])} | {ms(b['server_ms'])} |",
            f"| First pass of a fresh session | {ms(d['cold_round_trip_ms'])} | not measured |",
            f"| Tokens per chunk | {d['input_tokens']:.0f} in "
            f"| {b['input_tokens']:.0f} in, {b['output_tokens']:.0f} out |",
            "",
            "| Cost per 1,000 chunks | Decider 2B | Haiku 4.5 as judge |",
            "| --- | --- | --- |",
            f"| Per call | $0 | ${b['cost_per_1000_usd']:.2f} |",
            f"| Machine time | {d['machine_minutes_per_1000']:.1f} min on the machine below "
            "| none of yours |",
            "",
            f"Decider ran locally: {machine}, `{d['device']}` device, `{d['checkpoint']}`. "
            f"Haiku: `{b['model']}` on Bedrock, from `eval/results/bedrock_judge.csv`, over the "
            "network; its price is in `eval/run_bedrock_judge.py`.",
            "",
            f"Break-even: a machine rented by the hour costs less than the judge once it scans "
            f"more than **{e['chunks_per_hour']:.0f} chunks per hour**, for example an "
            f"`{e['instance']}` at ${e['usd_per_hour']}/h. It must then take at most "
            f"{e['max_seconds_per_chunk']:.1f} s per chunk; Decider's speed on that instance "
            "was not measured.",
            "",
        ]
    )


def plot(s: dict, path: Path = CHART) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    d, b = s["decider"], s["bedrock"]
    labels = [
        f"Decider 2B, local\n({d['machine'].get('cpu') or d['device']})\n$0 per call",
        f"Haiku 4.5 on Bedrock\n(judge)\n${b['cost_per_1000_usd']:.2f} per 1,000",
    ]
    p50 = [d["warm_round_trip_ms"][0], b["round_trip_ms"][0]]
    p95 = [d["warm_round_trip_ms"][1], b["round_trip_ms"][1]]
    x = range(len(labels))
    fig, ax = plt.subplots(figsize=(7, 4.2), dpi=150)
    bars50 = ax.bar([i - 0.18 for i in x], p50, width=0.36, label="p50", color="#2b6cb0")
    bars95 = ax.bar([i + 0.18 for i in x], p95, width=0.36, label="p95", color="#90cdf4")
    for bars in (bars50, bars95):
        ax.bar_label(bars, fmt="%.0f ms", padding=3, fontsize=9)
    ax.set_xticks(list(x), labels)
    ax.set_ylabel("Latency per chunk, round trip (ms)")
    ax.set_title(f"Checking one web-page chunk ({s['chunks']} chunks, 3 questions each)")
    ax.legend(frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_ylim(0, max(p95) * 1.2)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=2, help="passes over the chunks")
    parser.add_argument("--plot", action="store_true", help="also draw the chart (matplotlib)")
    parser.add_argument(
        "--plot-only", action="store_true", help="redraw the chart from latency_cost.json"
    )
    parser.add_argument("--out", type=Path, default=RESULTS)
    args = parser.parse_args(argv)

    if args.plot_only:
        s = json.loads((args.out / "latency_cost.json").read_text(encoding="utf-8"))
        print(f"Chart: {plot(s)}")
        return 0

    from guardrails.decider import DeciderClient

    client = DeciderClient()
    server = client.health()
    rows = measure_decider(client, load_dataset(DATASET), args.runs)
    s = summary(rows, read_bedrock(BEDROCK_CSV, DEFAULT_MODEL), server, hardware())

    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "latency_cost.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(rows[0])))
        writer.writeheader()
        writer.writerows(asdict(r) for r in rows)
    (args.out / "latency_cost.json").write_text(
        json.dumps(s, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
    )
    md = markdown(s)
    (args.out / "latency_cost.md").write_text(md, encoding="utf-8")
    print(md)
    if args.plot:
        print(f"Chart: {plot(s)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
