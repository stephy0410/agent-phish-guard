"""The calibration math replays ScanPolicy and sweeps thresholds; no Decider needed."""

from eval.calibrate_thresholds import (
    CURRENT,
    PolicyParams,
    evaluate,
    flags,
    lowest_at_fpr,
    sweep,
)


def row(technique, is_attack, visibility, instructs, req=0.0, auth=0.0, action=None):
    return {
        "technique": technique,
        "visibility": visibility,
        "is_attack": is_attack,
        "expected_action": action or ("flag_chunk" if is_attack else "allow"),
        "shown": f"{technique} sample",
        "probabilities": {
            "instructs_ai": instructs,
            "requests_user_data": req,
            "claims_authority": auth,
        },
    }


def test_flags_alone_rule():
    p = PolicyParams()
    # instructs_ai >= 0.9 flags on its own, whatever the other signals
    assert flags(
        {"instructs_ai": 0.91, "requests_user_data": 0.0, "claims_authority": 0.0}, "visible", p
    )
    assert not flags(
        {"instructs_ai": 0.89, "requests_user_data": 0.0, "claims_authority": 0.0}, "visible", p
    )


def test_flags_paired_rule_uses_visibility_bar():
    p = PolicyParams()
    probs = {"instructs_ai": 0.4, "requests_user_data": 0.9, "claims_authority": 0.0}
    assert not flags(probs, "visible", p)  # 0.4 < 0.5 visible bar
    assert flags(probs, "hidden", p)  # 0.4 >= 0.35 unseen bar, and req >= 0.5


def test_flags_paired_rule_needs_a_secondary_signal():
    p = PolicyParams()
    probs = {"instructs_ai": 0.8, "requests_user_data": 0.2, "claims_authority": 0.2}
    assert not flags(probs, "visible", p)  # instructs_ai high but no data/authority, and < 0.9


def test_evaluate_counts_recall_and_fpr():
    rows = [
        row("a", True, "visible", 0.95),  # TP via alone
        row("b", True, "visible", 0.4),  # FN (nothing fires)
        row("c", False, "visible", 0.95),  # FP via alone
        row("d", False, "visible", 0.1),  # TN
        row("frag", True, "visible", 0.1, action="block_tool_call"),  # excluded
    ]
    e = evaluate(rows, CURRENT)
    assert (e["tp"], e["fn"], e["fp"], e["tn"]) == (1, 1, 1, 1)
    assert e["recall"] == 0.5
    assert e["false_positive_rate"] == 0.5
    assert [m["technique"] for m in e["missed"]] == ["b"]
    assert [m["technique"] for m in e["false_alarms"]] == ["c"]


def test_lowering_alone_recovers_attacks():
    rows = [
        row("hit", True, "visible", 0.85),  # recovered when alone <= 0.85
        row("benign", False, "visible", 0.6),
    ]
    assert evaluate(rows, CURRENT)["recall"] == 0.0  # 0.85 < 0.9
    assert evaluate(rows, PolicyParams(instructs_ai_alone=0.8))["recall"] == 1.0


def test_sweep_and_lowest_at_fpr():
    # Only the alone rule applies here (no secondary signal on any row).
    rows = [
        row("atk_hi", True, "visible", 0.95),
        row("atk_mid", True, "visible", 0.82),
        row("ben_hi", False, "visible", 0.90),
        row("ben_lo", False, "visible", 0.10),
    ]
    pts = sweep(rows, "instructs_ai_alone", CURRENT, [0.80, 0.85, 0.90, 0.95])
    by_value = {pt["value"]: pt for pt in pts}
    # alone=0.95: only the 0.95 attack fires; the 0.90 benign is below it.
    assert by_value[0.95]["recall"] == 0.5
    assert by_value[0.95]["false_positive_rate"] == 0.0
    # alone=0.80: both attacks fire, but so does the 0.90 benign.
    assert by_value[0.80]["recall"] == 1.0
    assert by_value[0.80]["false_positive_rate"] == 0.5
    # Best recall at zero false positives: only thresholds above the 0.90 benign qualify.
    best = lowest_at_fpr(pts, fpr_max=0.0)
    assert best["false_positive_rate"] == 0.0
    assert best["recall"] == 0.5


def test_flags_matches_scanpolicy():
    """calibrate.flags must agree with the real ScanPolicy.flags, so the sweep can't drift from
    the policy it calibrates. Skipped where guardrails isn't importable; needs no Decider."""
    import pytest

    scanner = pytest.importorskip("guardrails.scanner")

    class Chunk:
        def __init__(self, visibility):
            self.visibility = visibility

    param_sets = [
        {},
        {"instructs_ai_alone": 0.82, "requests_user_data": 0.40},
        {
            "instructs_ai": 0.4,
            "instructs_ai_unseen": 0.3,
            "instructs_ai_alone": 0.75,
            "requests_user_data": 0.45,
            "claims_authority": 0.6,
        },
    ]
    levels = [0.0, 0.33, 0.35, 0.49, 0.5, 0.79, 0.82, 0.9, 1.0]
    for ps in param_sets:
        mine, theirs = PolicyParams(**ps), scanner.ScanPolicy(**ps)
        for vis in ("visible", "hidden", "metadata"):
            for ia in levels:
                for rq in (0.0, 0.5, 0.9):
                    for au in (0.0, 0.5, 0.9):
                        probs = {
                            "instructs_ai": ia,
                            "requests_user_data": rq,
                            "claims_authority": au,
                        }
                        assert flags(probs, vis, mine) == theirs.flags(Chunk(vis), probs)
