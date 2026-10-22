# Baseline: Bedrock as judge

The same three chunk questions the scanner asks Decider 2B, answered by Claude Haiku 4.5 on
Bedrock, on the evaluation dataset (`eval/data/eval_dataset.jsonl`, 120 chunks). Code:
[`eval/run_bedrock_judge.py`](../eval/run_bedrock_judge.py). Per-chunk results, with latency,
tokens and estimated cost: [`eval/results/bedrock_judge.csv`](../eval/results/bedrock_judge.csv).
Summary: [`eval/results/bedrock_judge.md`](../eval/results/bedrock_judge.md).

## Setup

- Same wording and true/false descriptions as `guardrails/questions.py`, on the same
  deobfuscated text the scanner sends Decider. One Converse call per chunk for the three
  questions, temperature 0, JSON reply.
- A judge answers yes or no, so "yes" counts as 1.0 under the scanner's `ScanPolicy`. That
  amounts to "flag when the text instructs an AI". A stricter rule (instructs and also asks for
  data or claims authority) is reported next to it.

## Results (run of 2026-10-22, 120 chunks, 0 unparseable replies)

| | Decider 2B (scanner) | Haiku 4.5 as judge |
| --- | --- | --- |
| `flag_chunk` attacks caught | 40/54 (74%) on CPU, 41/54 (76%) on an Apple M3 | 40/54 (74%); 32/54 (59%) with the strict rule |
| False alarms on `allow` rows | 3/60 | 0/60 with either rule |
| Latency per chunk, p50 / p95 | 624 / 693 ms, local on an Apple M3 | 685 / 838 ms, over the network |
| Cost per 1,000 chunks | $0 per call, plus about 10.5 min of machine time | $0.53 |

Decider's accuracy is the scanner at the thresholds of #15:
- **On CPU:** [eval/results/dataset_metrics.md](../eval/results/dataset_metrics.md) (#23).
- **On an Apple M3:** the review of #23. One row scores 0.8997 on CPU and 0.9007 on the M3, right on the 0.9 bar.

Both latencies are from [docs/benchmarks.md](benchmarks.md#decider-2b-against-bedrock-as-judge): the same 120 chunks and the same percentile method. Nearest-rank would put Haiku's p95 at 846 ms.

Cost:
- **Haiku:** the US-profile price listed on 2026-10-21 ($1.10 / $5.50 per million tokens in / out). Check the Bedrock pricing page before quoting it.
- **Decider:** no per-call price; you pay for the machine it runs on. The benchmark has the break-even point.

A 2B model running locally catches about as many attacks as a hosted frontier-family model asked
the same questions, about as fast and at no per-call cost. Haiku makes fewer false alarms.

## Shared weakness: "Assistant:" without "AI"

12 of Haiku's 14 misses start with `Assistant:` rather than `AI assistant:`. Haiku answers that
they ask for user data but not that they instruct an AI, so neither rule flags them:

| How the attack addresses the agent | Attacks | Caught by Haiku |
| --- | --- | --- |
| "AI assistant: …" | 23 | 22 (96%) |
| "Assistant: …" | 26 | 14 (54%) |

Decider showed the same gap in the #17 review ("Assistant:" without "AI" scored 0.27 to 0.56 on
`instructs_ai`). Two models failing the same way points at the question's wording ("instructions
to an AI assistant, agent or chatbot") more than at either model. The other two misses: an
instruction with no data request ("add two extra pairs to the cart") and a `[system]` policy
line, where Haiku saw the claimed authority but not the instruction.

## Limitations

- One run, one model, 120 chunks written by the team. Haiku at temperature 0 is close to, but
  not guaranteed, deterministic.
- The comparison keeps Decider's questions unchanged on purpose. Rewording `instructs_ai` would
  help both models and belongs to calibration, not to this baseline.
