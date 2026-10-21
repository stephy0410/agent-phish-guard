# Evaluation dataset

`eval_dataset.csv` is the file we edit by hand. `eval_dataset.jsonl` is generated from it in the
`LabeledChunk` shape with `uv run python -m eval.build_dataset_jsonl`, and
`tests/test_dataset.py` checks that every row loads as a `contracts.LabeledChunk`.

## Columns

`text, label, technique, visibility, vector, intent, evasion, expected_action, notes`

- `label` — `attack` or `benign`; `is_attack` in the JSONL is derived from it.
- `technique` — taxonomy slug. Benign rows carry the class they imitate, so false positives can
  be reported per technique.
- `visibility` / `vector` — where the text sits. The scanner gives `hidden` and `metadata` text
  a lower bar, so this (not the technique) drives the policy.
- `intent` — attacks only; `null` for benign.
- `evasion` — `plain` or `obfuscated` (zero-width, homoglyph, base64, fragmentation).
- `expected_action` — `allow` (benign), `flag_chunk`, or `block_tool_call`. Fragmentation rows
  are `block_tool_call`: a split payload is caught at the tool-call level, so its harmless second
  half is not counted as a per-chunk miss.

## How the rows were written

- 60 attacks + 60 benign, 6 per technique on each side.
- 11 attacks use non-exfiltration intents (cart, subscribe, sold-out, ranking, budget) to test
  the `instructs_ai >= 0.9` rule; the other 49 are data exfiltration.
- Benign rows lean on imperative verbs (recipes, how-tos, buy buttons, imperative reviews) and
  include ~10 hard negatives that mention an email, card, address, or the store's assistant —
  the texts that set the thresholds.
- Payloads are distinct per row and are **not** copied from the sandbox attack pages or
  `eval/question_examples.json`, which tuned the scanner thresholds.
- Fictional brand (Lumora), reserved `.example` emails, invented names. No real personal data.

## Calibration note

Split train/test **by payload, not by row**: the same idea is phrased several ways across
techniques, so a random row split would leak. Hold out on the underlying payload instead.
