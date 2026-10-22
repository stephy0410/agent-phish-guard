"""Render a demo trace as a self-contained HTML page, for the video.

    uv run python -m agent.trace_view                         # every trace in eval/results/traces
    uv run python -m agent.trace_view path/to/trace.json      # one or more traces

Writes ``<trace>.html`` next to each JSON trace (``agent.demo`` also does it on every run). The
page needs no server and no network: open it in a browser and record.

What it shows, in the order the agent did it:

- every page the agent opened, and with the chunk scanner every chunk of it: where it was
  (visible, hidden, metadata), Decider's answer to each question with its probability and
  confidence, and the action. Quarantined chunks are red, with what the model got instead;
- every call to ``send_data``, with the tool guard's verdict (red deny, amber confirm, green
  proceed) and Decider's answers;
- the outcome at the top: red when user data reached the attacker, green when it didn't.
"""

from __future__ import annotations

import html
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

from agent.demo import MODE_TITLES, TRACES, Step, Trace

QUESTION_LABELS = {
    "instructs_ai": "Instructs an AI",
    "requests_user_data": "Asks for user data",
    "claims_authority": "Claims authority",
    "args_grounded": "Args from the user",
    "serves_user_request": "Serves the request",
}
VISIBILITY_LABELS = {"visible": "visible", "hidden": "hidden", "metadata": "metadata"}

CSS = (Path(__file__).parent / "trace_view.css").read_text(encoding="utf-8")

TOGGLE_JS = """
document.getElementById('toggle').addEventListener('click', function () {
  var on = document.body.classList.toggle('only-blocked');
  this.textContent = on ? 'Show every chunk' : 'Show only blocked chunks';
});
"""


def esc(text: Any) -> str:
    return html.escape(str(text), quote=True)


def show_text(text: str) -> str:
    """Escape text and make invisible characters visible as small red markers."""
    out = []
    for ch in text:
        if unicodedata.category(ch) == "Cf":  # zero-width, tag and other format characters
            out.append(f'<span class="inv">U+{ord(ch):04X}</span>')
        else:
            out.append(esc(ch))
    return "".join(out)


def say(text: str) -> str:
    """The agent's message: escaped, with markdown **bold** shown as bold."""
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc(text))


def prob_cell(decision: dict[str, Any] | None) -> str:
    if not decision:
        return '<td class="p">—</td>'
    p = float(decision["p_true"])
    hi = ' class="hi"' if p >= 0.5 else ""
    return (
        f'<td class="p"><span class="pv">{p:.2f} <small>conf {float(decision["confidence"]):.2f}'
        f'</small></span><div class="bar"><i{hi} style="width:{p * 100:.0f}%"></i></div></td>'
    )


def chunk_table(chunks: list[dict[str, Any]]) -> str:
    questions = ["instructs_ai", "requests_user_data", "claims_authority"]
    head = "".join(f"<th>{esc(QUESTION_LABELS[q])}</th>" for q in questions)
    rows = []
    for c in chunks:
        by_q = {d["question"]: d for d in c.get("decisions", [])}
        deny = c["action"] == "deny"
        instead = (
            f'<span class="instead">Model got: {esc(c["shown_to_model"])}</span>' if deny else ""
        )
        badge = (
            '<span class="badge deny">QUARANTINED</span>'
            if deny
            else '<span class="badge proceed">passed</span>'
        )
        vis = c.get("visibility", "")
        rows.append(
            f'<tr class="{"deny" if deny else "proceed"}">'
            f'<td class="where"><span class="vis {esc(vis)}">{esc(VISIBILITY_LABELS.get(vis, vis))}'
            f"</span><br>{esc(c.get('source') or '')}</td>"
            f'<td class="text">{show_text(c["text"])}{instead}</td>'
            + "".join(prob_cell(by_q.get(q)) for q in questions)
            + f"<td>{badge}</td></tr>"
        )
    return (
        "<table><thead><tr><th>Where</th><th>Chunk</th>"
        f"{head}<th>Action</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )


def page_text(text: str, recipients: dict[str, str]) -> str:
    """The unchecked page; lines naming a recipient of the agent's data are highlighted.

    ``recipients`` maps an address to the note shown under the line that names it.
    """
    lines = []
    for line in text.split("\n"):
        hit = next((r for r in recipients if r and r.lower() in line.lower()), None)
        if hit:
            note = f"↑ {recipients[hit]}"
            lines.append(f'<mark class="attack">{show_text(line)}<small>{note}</small></mark>')
        else:
            lines.append(show_text(line))
    return "\n".join(lines)


def browse_card(
    step: Step,
    result: Step | None,
    chunks: list[dict[str, Any]] | None,
    recipients: dict[str, str] | None = None,
    scanned_mode: bool = False,
) -> str:
    url = (step.input or {}).get("url", "")
    if chunks:
        blocked = sum(c["action"] == "deny" for c in chunks)
        count = (
            f'<span class="count"><b>{blocked} quarantined</b> of {len(chunks)} chunks</span>'
            if blocked
            else f'<span class="count">{len(chunks)} chunks, none quarantined</span>'
        )
        body = chunk_table(chunks)
        if not blocked:  # nothing to see for the video: collapsed, one click to open
            body = f"<details><summary>Show the {len(chunks)} chunks</summary>{body}</details>"
    else:
        count = ""
        text = result.text if result else ""
        note = (
            "This trace was recorded before chunk details were saved; "
            "this is the page text after the scanner."
            if scanned_mode
            else "No scanner: the model got this page text unchecked."
        )
        body = (
            f'<p class="unchecked">{note}</p>'
            f'<pre class="page">{page_text(text, recipients or {})}</pre>'
        )
    return (
        f'<div class="card"><header><span class="tool">browse_page</span>'
        f'<span class="arg">{esc(url)}</span>{count}</header><div class="body">{body}</div></div>'
    )


def guard_block(decision: dict[str, Any] | None) -> str:
    if not decision:
        return ""
    kind = decision["type"]
    label = {"deny": "DENIED", "confirm": "ASKED THE USER", "proceed": "ALLOWED"}.get(kind, kind)
    probs = "".join(
        f'<span class="pv">{esc(QUESTION_LABELS.get(d["question"], d["question"]))}: '
        f"{float(d['p_true']):.2f} <small>conf {float(d['confidence']):.2f}</small></span>"
        for d in decision.get("decisions", [])
    )
    asked = (
        f'<div class="why">Asked: “{esc(decision["question"])}” · user answered '
        f"{esc(decision['user_answer'])}</div>"
        if decision.get("question")
        else ""
    )
    return (
        f'<div class="verdict {esc(kind)}"><span class="badge {esc(kind)}">Tool guard: {esc(label)}'
        f'</span>{probs}<div class="why">{esc(decision.get("reason") or "")}</div>{asked}</div>'
    )


def no_guard_block(step: Step, guarded: bool) -> str:
    if guarded or step.tool != "send_data":
        return ""
    return (
        '<div class="verdict none"><span class="badge leak">No tool guard</span>'
        '<span class="pv">the call ran without any check</span></div>'
    )


def tool_card(
    step: Step, result: Step | None, decision: dict[str, Any] | None, guarded: bool = True
) -> str:
    args = json.dumps(step.input or {}, indent=2, ensure_ascii=False)
    res = ""
    if result is not None:
        failed = result.status not in (None, "success")
        cls = "err" if failed else ("sent" if step.tool == "send_data" else "")
        mark = "✕ " if failed else "→ "
        res = f'<div class="result {cls}">{mark}{show_text(result.text)}</div>'
    return (
        f'<div class="card"><header><span class="tool">{esc(step.tool)}</span></header>'
        f'<div class="body"><pre class="kv">{esc(args)}</pre></div>'
        f"{guard_block(decision) or no_guard_block(step, guarded)}{res}</div>"
    )


def render(trace: Trace) -> str:
    """The whole page for one trace."""
    results = {s.tool_use_id: s for s in trace.steps if s.kind == "tool_result"}
    recipients = {
        d["recipient"]: "the agent tried to send your data here; the tool guard denied it"
        for d in trace.guard_decisions
        if d["type"] == "deny"
    }
    recipients.update(
        {a["recipient"]: "the agent sent your data to this address" for a in trace.leaks}
    )
    guarded = trace.mode != "none"
    pending = list(trace.scanned)
    guard = list(trace.guard_decisions)
    items = []
    for step in trace.steps:
        if step.kind == "assistant":
            items.append(
                f'<li><div class="say"><span class="who">Agent</span>{say(step.text)}</div></li>'
            )
        elif step.kind == "tool_call":
            result = results.get(step.tool_use_id)
            if step.tool == "browse_page":
                url = None
                chunks: list[dict[str, Any]] = []
                while pending and (url is None or pending[0]["url"] == url):
                    url = pending[0]["url"]
                    if chunks and pending[0]["index"] == 0:
                        break  # the same page opened again: a new scan starts at index 0
                    chunks.append(pending.pop(0))
                card = browse_card(
                    step, result, chunks or None, recipients, scanned_mode=trace.mode == "full"
                )
                items.append(f"<li>{card}</li>")
            else:
                decision = guard.pop(0) if step.tool == "send_data" and guard else None
                items.append(f"<li>{tool_card(step, result, decision, guarded)}</li>")

    leak = trace.leaked
    outcome = (
        f'<div class="outcome {"leak" if leak else "safe"}"><span class="tag">'
        f"{'DATA LEAKED' if leak else 'PROTECTED'}</span>{esc(trace.outcome)}</div>"
    )
    toggle = (
        '<button class="toggle" id="toggle">Show only blocked chunks</button>'
        if trace.scanned
        else ""
    )
    legend = (
        '<div class="legend"><span><i class="sw red"></i>blocked or leaked</span>'
        '<span><i class="sw amber"></i>asked the user</span>'
        '<span><i class="sw green"></i>allowed / safe</span>'
        "<span>Probabilities are Decider's P(yes); conf = |2p − 1|</span>"
        f"{toggle}</div>"
    )
    sends = (
        "".join(
            f"<li>{esc(a['recipient'])}: "
            f"{esc(', '.join(a['leaked_fields']) or 'no user data')}</li>"
            for a in trace.send_attempts
        )
        or "<li><code>send_data</code> never ran</li>"
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{esc(MODE_TITLES[trace.mode])} — Agent Phish Guard</title>"
        f"<style>{CSS}</style></head><body><main>"
        f"<h1>{esc(MODE_TITLES[trace.mode])}</h1>"
        f'<p class="sub">Agent Phish Guard · Lumora demo store · <code>{esc(trace.model_id)}</code>'
        f" · {trace.seconds} s · {esc(trace.started_at)}</p>"
        f'<p class="prompt"><b>User:</b> {esc(trace.prompt)}</p>'
        f'{outcome}{legend}<ol class="steps">{"".join(items)}</ol>'
        f'<section class="summary"><h2>What left the agent</h2><ul>{sends}</ul></section>'
        f"</main><script>{TOGGLE_JS if trace.scanned else ''}</script></body></html>\n"
    )


def write_html(json_path: Path) -> Path:
    trace = Trace.from_dict(json.loads(json_path.read_text(encoding="utf-8")))
    out = json_path.with_suffix(".html")
    out.write_text(render(trace), encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    paths = [Path(a) for a in args] or sorted(TRACES.glob("*.json"))
    for path in paths:
        print(write_html(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
