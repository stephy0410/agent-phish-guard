"""The evaluation dataset must be balanced, unique and valid LabeledChunks."""

from collections import Counter

import pytest

from contracts import LabeledChunk
from eval.build_dataset_jsonl import rows_from_csv

RECORDS = rows_from_csv()


def test_sixty_sixty_balance():
    labels = Counter(r["is_attack"] for r in RECORDS)
    assert labels[True] == 60
    assert labels[False] == 60


def test_six_attacks_per_technique():
    per = Counter(r["technique"] for r in RECORDS if r["is_attack"])
    assert set(per.values()) == {6}
    assert len(per) == 10


def test_no_duplicate_text():
    texts = [r["chunk"]["text"] for r in RECORDS]
    assert len(texts) == len(set(texts))


@pytest.mark.parametrize("record", RECORDS)
def test_every_row_is_a_valid_labeled_chunk(record):
    LabeledChunk.model_validate(record)


def test_benign_rows_have_no_intent():
    for r in RECORDS:
        if not r["is_attack"]:
            assert r["intent"] is None
            assert r["expected_action"] == "allow"


def test_attacks_cover_more_than_exfiltration():
    intents = {r["intent"] for r in RECORDS if r["is_attack"]}
    assert "data_exfiltration" in intents
    assert len(intents) >= 3
