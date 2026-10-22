"""The HTML trace viewer, on scripted runs (no Bedrock, no Decider server)."""

import json

import pytest

from agent.demo import build_setup, run, save
from agent.trace_view import main, render, show_text
from tests.test_demo import ATTACKER, FakeDecider, ScriptedModel
from tools.sensitive import clear_send_attempts


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("BROWSE_ROOT", raising=False)
    clear_send_attempts()
    yield
    clear_send_attempts()


def trace_for(mode):
    return run(build_setup(mode, decider=FakeDecider()), model=ScriptedModel())


def test_full_trace_records_every_chunk_with_decisions():
    trace = trace_for("full")
    assert len(trace.scanned) > 30  # store page + Onyx page, links included
    chunk = trace.scanned[0]
    assert {"url", "visibility", "source", "text", "action", "decisions"} <= set(chunk)
    assert {d["question"] for d in chunk["decisions"]} == {
        "instructs_ai",
        "requests_user_data",
        "claims_authority",
    }
    assert all(0 <= d["confidence"] <= 1 for d in chunk["decisions"])
    denied = [c for c in trace.scanned if c["action"] == "deny"]
    assert len(denied) == 2 and all(c["shown_to_model"] != c["text"] for c in denied)
    assert trace.guard_decisions[0]["decisions"]  # the guard's Decider answers too


def test_page_shows_each_chunk_decision_confidence_and_action():
    trace = trace_for("full")
    page = render(trace)
    assert page.count('<tr class="deny">') == 2
    assert page.count("QUARANTINED") == 2
    assert page.count('<tr class="proceed">') == len(trace.scanned) - 2
    assert "conf 0.80" in page and "conf 0.90" in page  # every probability with confidence
    assert "Model got:" in page
    assert "Tool guard: DENIED" in page
    assert "PROTECTED" in page and "DATA LEAKED" not in page
    assert "<details>" in page  # the page with nothing blocked is collapsed


def test_unguarded_page_shows_the_leak_and_where_it_came_from():
    page = render(trace_for("none"))
    assert "DATA LEAKED" in page
    assert "No scanner: the model got this page text unchecked." in page
    assert page.count('<mark class="attack">') == 1  # the hidden div naming the attacker
    assert "No tool guard" in page
    assert ATTACKER in page


def test_tool_guard_only():
    page = render(trace_for("tool"))
    assert "Tool guard: DENIED" in page and "PROTECTED" in page
    assert '<tr class="deny">' not in page  # no scanner, so no chunk table
    assert page.count('<mark class="attack">') == 1  # where the denied address came from
    assert "the tool guard denied it" in page


def test_agent_markdown_bold():
    from agent.trace_view import say

    assert say("the **Onyx** <b>") == "the <b>Onyx</b> &lt;b&gt;"


def test_a_failed_browse_takes_no_chunks():
    """Stephy's case in #26: /, then /checkout (not a page), then Onyx."""
    from tests.test_demo import ONYX
    from tools.browse import BASE_URL

    model = ScriptedModel(
        [
            ("browse_page", {"url": BASE_URL}),
            ("browse_page", {"url": f"{BASE_URL}checkout"}),
            ("browse_page", {"url": ONYX}),
            "Done.",
        ]
    )
    trace = run(build_setup("full", decider=FakeDecider()), model=model)
    page = render(trace)
    cards = page.split('<span class="tool">browse_page</span>')[1:]
    assert len(cards) == 3
    home, checkout, onyx = cards
    assert "QUARANTINED" not in home
    assert "The page didn" in checkout and "Cannot open" in checkout
    assert "<table>" not in checkout and "QUARANTINED" not in checkout
    assert onyx.count("QUARANTINED") == 2  # Onyx keeps its own chunks


def test_the_same_page_opened_twice_gets_two_tables():
    from tests.test_demo import ONYX

    model = ScriptedModel([("browse_page", {"url": ONYX}), ("browse_page", {"url": ONYX}), "Ok."])
    trace = run(build_setup("full", decider=FakeDecider()), model=model)
    cards = render(trace).split('<span class="tool">browse_page</span>')[1:]
    assert [c.count("QUARANTINED") for c in cards] == [2, 2]


def test_text_is_escaped_and_invisible_characters_are_shown():
    assert show_text("<b>x</b>") == "&lt;b&gt;x&lt;/b&gt;"
    assert show_text("a​b") == 'a<span class="inv">U+200B</span>b'
    assert "U+E0041" in show_text("ok\U000e0041")


def test_page_is_self_contained():
    page = render(trace_for("full"))
    for external in ("<link ", "src=", "http://cdn", "https://"):
        assert external not in page.replace("http://localhost:8000", "")


def test_save_writes_html_and_main_rerenders_old_traces(tmp_path):
    trace = trace_for("full")
    json_path, _ = save(trace, tmp_path)
    html_path = json_path.with_suffix(".html")
    assert html_path.read_text(encoding="utf-8") == render(trace)

    # An older trace without "scanned" still renders.
    data = json.loads(json_path.read_text(encoding="utf-8"))
    data.pop("scanned")
    old = tmp_path / "old.json"
    old.write_text(json.dumps(data), encoding="utf-8")
    assert main([str(old)]) == 0
    page = old.with_suffix(".html").read_text(encoding="utf-8")
    assert "Tool guard: DENIED" in page
    assert "No scanner" not in page  # a full-mode trace never claims the page went unchecked
    assert "recorded before chunk details were saved" in page
