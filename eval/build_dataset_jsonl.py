"""Write eval/data/eval_dataset.jsonl in the LabeledChunk shape, from the CSV we edit by hand.

    uv run python -m eval.build_dataset_jsonl

Each CSV row becomes one record. tests/test_dataset.py checks that every record validates
against contracts.LabeledChunk, so the CSV stays in the interface contract's shape.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).parent
CSV_PATH = HERE / "data" / "eval_dataset.csv"
JSONL_PATH = HERE / "data" / "eval_dataset.jsonl"
BASE_URL = "https://shop.example/dataset"

# Chunk.source that matches each technique's placement on a page.
SOURCE = {
    "css_hidden": "div",
    "html_comment": "comment",
    "alt_aria": "img[alt]",
    "meta_tag": "meta[name=description]",
    "zero_width": "div.review",
    "homoglyph": "div.review",
    "ugc_injection": "div.review",
    "fake_system": "div",
    "fragmentation": "div.review",
    "encoded": "div.review",
}


def _chunk_id(index: int) -> str:
    digest = hashlib.sha1(BASE_URL.encode("utf-8")).hexdigest()[:8]
    return f"{digest}:{index}"


def labeled_chunk(row: dict[str, str], index: int) -> dict[str, object]:
    """One CSV row as a LabeledChunk-shaped dict."""
    return {
        "chunk": {
            "id": _chunk_id(index),
            "url": BASE_URL,
            "index": index,
            "text": row["text"],
            "visibility": row["visibility"],
            "source": SOURCE[row["technique"]],
        },
        "is_attack": row["label"] == "attack",
        "vector": row["vector"],
        "technique": row["technique"],
        "intent": row["intent"] or None,
        "evasion": row["evasion"],
        "expected_action": row["expected_action"],
        "notes": row["notes"] or None,
    }


def rows_from_csv(path: Path = CSV_PATH) -> list[dict[str, object]]:
    with path.open(encoding="utf-8", newline="") as f:
        return [labeled_chunk(row, i) for i, row in enumerate(csv.DictReader(f))]


def to_jsonl(records: list[dict[str, object]]) -> str:
    """Serialize records as JSON Lines. ensure_ascii so invisible characters show as \\u escapes
    in diffs. main() and tests/test_dataset.py use this, so the file can't drift from the CSV."""
    return "".join(json.dumps(record, ensure_ascii=True) + "\n" for record in records)


def main() -> None:
    records = rows_from_csv()
    JSONL_PATH.write_text(to_jsonl(records), encoding="utf-8")
    print(f"Wrote {len(records)} records to {JSONL_PATH}")


if __name__ == "__main__":
    main()
