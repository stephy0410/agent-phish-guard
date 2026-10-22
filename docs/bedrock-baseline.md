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
| `flag_chunk` attacks caught | 41/54 (76%) | 40/54 (74%) |
| False alarms on `allow` rows | 3/60 | 0/60 |
| Latency per chunk | about 0.6 s, local on an Apple M3 | median 685 ms, p95 846 ms, over the network |
| Cost | no per-call cost; runs on the machine | about $0.000534 per chunk, $0.53 per 1,000 |

Decider's row is the scanner's first run on this dataset with the thresholds of #15, reported in
the review of #17; its latency is from [docs/scanner.md](scanner.md). Haiku's cost uses the
US-profile price listed on 2026-10-21 ($1.10 / $5.50 per million tokens in / out); check the
Bedrock pricing page before quoting it.

A 2B model running locally catches about as many attacks as a hosted frontier-family model asked
the same questions, at no per-call cost. Haiku makes fewer false alarms.

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
