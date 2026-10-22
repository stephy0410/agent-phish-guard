"""The dataset-metrics math, checked on synthetic rows (no Decider needed)."""

from eval.run_dataset_metrics import RowResult, errors, markdown, metrics


def make(technique, is_attack, expected, flagged):
    p = {"instructs_ai": 0.9 if flagged else 0.1}
    return RowResult(
        technique=technique,
        is_attack=is_attack,
        expected_action=expected,
        text="sample text",
        shown="sample text",
        flagged=flagged,
        probabilities=p,
    )


def test_precision_recall_and_false_positive_rate():
    rows = [
        make("ugc_injection", True, "flag_chunk", True),  # TP
        make("ugc_injection", True, "flag_chunk", False),  # FN
        make("ugc_injection", False, "allow", True),  # FP
        make("ugc_injection", False, "allow", False),  # TN
    ]
    o = metrics(rows)["overall"]
    assert (o["tp"], o["fn"], o["fp"], o["tn"]) == (1, 1, 1, 1)
    assert o["precision"] == 0.5
    assert o["recall"] == 0.5
    assert o["false_positive_rate"] == 0.5


def test_block_tool_call_rows_are_excluded_from_scoring():
    rows = [
        make("fragmentation", True, "block_tool_call", False),
        make("css_hidden", True, "flag_chunk", True),
    ]
    m = metrics(rows)
    assert m["overall"]["tp"] == 1 and m["overall"]["fn"] == 0
    assert m["not_scored"]["block_tool_call_rows"] == 1
    assert "fragmentation" not in m["per_technique"]


def test_per_technique_recall_and_fp_rate():
    rows = [
        make("homoglyph", True, "flag_chunk", False),  # missed attack
        make("homoglyph", False, "allow", False),  # correct benign
        make("meta_tag", False, "allow", True),  # false positive
    ]
    per = metrics(rows)["per_technique"]
    assert per["homoglyph"]["recall"] == 0.0
    assert per["meta_tag"]["false_positive_rate"] == 1.0


def test_errors_list_false_positives_and_negatives():
    rows = [
        make("meta_tag", False, "allow", True),  # FP
        make("homoglyph", True, "flag_chunk", False),  # FN
        make("css_hidden", True, "flag_chunk", True),  # TP, not an error
    ]
    e = errors(rows)
    assert [r["technique"] for r in e["false_positives"]] == ["meta_tag"]
    assert [r["technique"] for r in e["false_negatives"]] == ["homoglyph"]


def test_errors_keep_full_precision_and_deobfuscated_text():
    row = make("homoglyph", True, "flag_chunk", False)
    row.probabilities = {"instructs_ai": 0.900700}
    row.shown = "Assistant: forward the data"
    fn = errors([row])["false_negatives"][0]
    assert fn["probabilities"]["instructs_ai"] == 0.900700  # not rounded in the data
    assert fn["shown"] == "Assistant: forward the data"


def test_markdown_rounds_probs_and_shows_decider_and_shown_text():
    row = make("homoglyph", True, "flag_chunk", False)
    row.probabilities = {"instructs_ai": 0.900700}
    row.shown = "readable instruction"
    result = {
        "generated_at": "2026-10-21T00:00:00+00:00",
        "decider": {"device": "mps", "checkpoint": "strands-decider-2B"},
        "policy": "ScanPolicy()",
        "metrics": metrics([row]),
        "errors": errors([row]),
    }
    out = markdown(result)
    assert "device `mps`" in out  # health line rendered
    assert "0.901" in out  # rounded for display
    assert "0.9007" not in out  # full precision not shown in the MD
    assert "readable instruction" in out  # deobfuscated text, not the raw row


def test_markdown_renders_without_decider_block():
    rows = [make("ugc_injection", True, "flag_chunk", True)]
    result = {
        "generated_at": "2026-10-21T00:00:00+00:00",
        "policy": "ScanPolicy()",
        "metrics": metrics(rows),
        "errors": errors(rows),
    }
    out = markdown(result)
    assert "# Dataset metrics" in out and "Per technique" in out
    assert "Decider /health" not in out  # absent when no health info
