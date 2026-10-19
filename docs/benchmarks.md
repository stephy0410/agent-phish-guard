# Benchmarks

## Decider 2B baseline latency

How long one guardrail decision takes: one yes/no question about one web-page chunk, against a warm local server.

| Metric | Value |
| --- | --- |
| p50, server-side | **314 ms** |
| p50, including the HTTP round trip | 316 ms |
| Range over 10 runs (server-side) | 310–316 ms |
| Input tokens per request | 176 |

Measured on 2026-10-18 with `eval/bench_decider_latency.py`: 2 warm-up runs, then 10 measured runs. Every run sends a different chunk, so the server's prefix cache cannot reuse earlier work. Raw output: [`eval/results/decider_latency.json`](../eval/results/decider_latency.json).

### Setup

| | |
| --- | --- |
| Hardware | Apple M3 (base chip), 16 GB unified memory, MacBook |
| OS | macOS 15.7.1 |
| Decider | `strands-decider` 0.1.0 from PyPI, PyTorch on the `mps` device |
| Model | `StrandsAgents/strands-decider-2B-qwen3.5-v1-2610` (base `Qwen/Qwen3.5-2B-Base`), 4096-token window |

### Context

- Upstream reports a 153 ms warm median on an M3 Pro for tasks under 300 tokens, and 115 ms on an RTX 3090 ([strands-decider README](https://github.com/strands-labs/strands-decider#performance)). The base M3 has fewer GPU cores than the M3 Pro, which fits our roughly 2x figure.
- Upstream's MLX backend (`--device mlx`) is 1.4–1.6x faster than MPS on Apple silicon. It is not in the 0.1.0 release on PyPI yet.
- Latency grows roughly linearly with input tokens, so chunk size is the main lever we control.
- The first request at a new input length is slower on Apple silicon (a one-off kernel compile). The warm-up runs absorb it here; in the agent, expect the first few chunks of a session to be slower.

### Reproduce

```bash
uv tool install strands-decider
strands-decider serve StrandsAgents/strands-decider-2B-qwen3.5-v1-2610 --port 8099
uv run python -m eval.bench_decider_latency --runs 10
```
