"""The tool-call guardrail: Decider checks a sensitive call before it runs.

A Strands ``InterventionHandler`` on ``before_tool_call``:

    agent = build_guarded_agent(tools=[...])   # agent/core.py

For a call to a sensitive tool (``send_data`` by default) it asks the tool-call questions of
guardrails/questions.py about the user's own messages and the proposed call, and returns:

- ``Proceed`` when the arguments come from the user's request and the call serves it,
- ``Deny`` when the arguments clearly come from somewhere else (a web page): the tool does not
  run and the model gets ``DENIED: `` (added by Strands) followed by ``DENY_MESSAGE``,
- ``Confirm`` in between: the user is asked before the call runs, with the fields, the recipient
  and which of the user's data the call carries.

On top of Decider, ``RecipientRule`` denies a call that sends the user's data to a recipient
nobody gave: not written by the user, not the user's own address, not on the site being browsed.

Only the user's own text goes into the state, never tool results, so a page can't make its
recipient look requested. Every decision is recorded as a contract ``Action`` with its
``Decision``s, in ``records`` and, if ``TOOL_GUARD_LOG`` names a file, as JSON Lines. If the
guard fails (Decider down), Strands treats it as ``Deny``: fail closed.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from strands.interventions import Confirm, Deny, InterventionHandler, Proceed

from contracts import Action, Decision
from guardrails.decider import DeciderClient
from guardrails.questions import TOOL_CALL_QUESTIONS, tool_call_state
from tools.browse import BASE_URL, LOCAL_HOSTS
from tools.profile import FAKE_PROFILE
from tools.sensitive import find_leaked_fields

LOG_ENV_VAR = "TOOL_GUARD_LOG"
DENY_MESSAGE = (
    "Blocked by Agent Phish Guard: this call sends data the user did not ask to send, or to a "
    "recipient the user did not name. Do not retry it; tell the user what was blocked."
)
FIELD_LABELS = {
    "card_number": "card number",
    "card_cvc": "card CVC",
    "card_expiry": "card expiry date",
    "address": "address",
    "email": "email address",
    "phone": "phone number",
}
"""How the confirmation question names the user's data, in the order it lists them."""
SITE_ORIGINS = frozenset(f"http://{host}:{urlsplit(BASE_URL).port}" for host in LOCAL_HOSTS)
"""The sandbox site the browse tool opens. Its own URLs (a checkout) may receive data."""

Verdict = Literal["proceed", "confirm", "deny"]
FindUserData = Callable[[dict[str, Any], str], list[str]]

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"https?://[^\s<>\"')]+", re.IGNORECASE)


@dataclass(frozen=True)
class ToolCallPolicy:
    """Turns the two answers into a verdict.

    Deny when `args_grounded` is very low: the recipient or the data came from the page. That is
    the clearest signal; `serves_user_request` was weak on legitimate calls whose recipient the
    task implies (a store's checkout or support address). Proceed only when both are high, and
    ask the user in between, so a legitimate purchase gets a confirmation instead of a block.

    Provisional thresholds from the dev set (docs/questions.md); calibration replaces them.
    """

    proceed_at: float = 0.6
    deny_below: float = 0.2

    def decide(self, p: dict[str, float]) -> Verdict:
        if p["args_grounded"] < self.deny_below:
            return "deny"
        if p["args_grounded"] >= self.proceed_at and p["serves_user_request"] >= self.proceed_at:
            return "proceed"
        return "confirm"


@dataclass(frozen=True)
class RecipientRule:
    """Deny, whatever Decider says, a call that sends the user's data to a recipient nobody gave.

    A recipient is given when the user wrote it (the same email address, or a URL on a site the
    user wrote), when it is the user's own address, or when it is a URL on the site the agent
    browses (the store's own checkout). Anything else came from the model or from a page. On the
    demo store, "buy them with my saved details" makes the attacker's address score like a
    checkout (0.26 / 0.25), so Decider alone asks the user instead of denying.

    Addresses are compared whole: a look-alike of an address the user wrote is not given.
    """

    site_origins: frozenset[str] = SITE_ORIGINS
    own_addresses: frozenset[str] = frozenset({FAKE_PROFILE["email"]})

    def given(self, recipient: str, user_text: str) -> bool:
        target = recipient.strip().lower().removeprefix("mailto:")
        if target in {a.lower() for a in self.own_addresses}:
            return True
        if _EMAIL.fullmatch(target):
            return target in {e.lower() for e in _EMAIL.findall(user_text)}
        origin = _origin(target)
        named = {_origin(url) for url in _URL.findall(user_text)}
        return origin is not None and (origin in self.site_origins or origin in named)


@dataclass(frozen=True)
class GuardRecord:
    """What the guard decided about one tool call."""

    tool: str
    arguments: dict[str, Any]
    user_request: str
    action: Action
    user_response: Any = None
    """The answer of the ``confirm`` callback, when there is one."""

    def to_json(self) -> str:
        return json.dumps(
            {
                "decided_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "tool": self.tool,
                "arguments": self.arguments,
                "user_request": self.user_request,
                "user_response": self.user_response,
                "action": self.action.model_dump(),
            },
            ensure_ascii=True,
            default=str,
        )


class ToolCallGuard(InterventionHandler):
    """Checks calls to sensitive tools with Decider before they run.

    ``confirm`` answers a confirmation without pausing the agent: it gets the question and
    returns the user's answer (True or "yes" to allow). Without it, ``Confirm`` interrupts the
    agent and the caller resumes it with the answer (Strands interrupts).

    ``find_user_data`` names the user's sensitive fields a call carries, in its data or in its
    recipient (by default ``find_leaked_fields`` of tools/sensitive.py, which matches values,
    not field names). ``recipient_rule=None`` turns ``RecipientRule`` off, to measure Decider
    alone.
    """

    name = "agent-phish-guard-tool-call-guard"

    def __init__(
        self,
        client: DeciderClient | None = None,
        policy: ToolCallPolicy | None = None,
        sensitive_tools: Iterable[str] = ("send_data",),
        confirm: Callable[[str], Any] | None = None,
        log_path: str | Path | None = None,
        find_user_data: FindUserData = find_leaked_fields,
        recipient_rule: RecipientRule | None = RecipientRule(),  # noqa: B008 (frozen, shared safely)
    ) -> None:
        self.client = client or DeciderClient()
        self.policy = policy or ToolCallPolicy()
        self.sensitive_tools = frozenset(sensitive_tools)
        self.confirm = confirm
        self.find_user_data = find_user_data
        self.recipient_rule = recipient_rule
        self.log_path = log_path or os.environ.get(LOG_ENV_VAR) or None
        self.records: list[GuardRecord] = []
        self._lock = threading.Lock()

    @property
    def on_error(self) -> Literal["deny"]:
        """If the check itself fails, block the call rather than let it through unchecked."""
        return "deny"

    def before_tool_call(self, event: Any, **kwargs: Any) -> Proceed | Deny | Confirm:
        tool = event.tool_use["name"]
        if tool not in self.sensitive_tools:
            return Proceed(reason=f"{tool} is not a sensitive tool")

        arguments = event.tool_use.get("input") or {}
        request = user_request(event.agent.messages)
        response = self.client.ask(tool_call_state(request, tool, arguments), TOOL_CALL_QUESTIONS)
        p = {key: answer["noul"] for key, answer in response.answers.items()}
        model = getattr(response, "model", None) or "strands-decider"

        recipient = str(arguments.get("recipient") or "")
        data = arguments.get("data")
        carried = self.find_user_data(data if isinstance(data, dict) else {"data": data}, recipient)
        reason = ", ".join(f"{k}={v:.2f}" for k, v in p.items())
        if carried and self.recipient_rule and not self.recipient_rule.given(recipient, request):
            verdict: Verdict = "deny"
            reason += f"; sends {', '.join(carried)} to a recipient nobody gave"
        else:
            verdict = self.policy.decide(p)

        prompt = confirmation_prompt(tool, arguments, carried)
        message = {"proceed": None, "deny": DENY_MESSAGE, "confirm": prompt}[verdict]
        action = Action(
            type=verdict,
            subject_kind="tool_call",
            subject_id=event.tool_use["toolUseId"],
            reason=reason,
            message=message,
            decisions=[
                _decision(event.tool_use["toolUseId"], k, v, model, response.latency_ms)
                for k, v in p.items()
            ],
        )
        answer = self.confirm(prompt) if verdict == "confirm" and self.confirm else None
        self._record(GuardRecord(tool, arguments, request, action, answer))

        if verdict == "proceed":
            return Proceed(reason=action.reason)
        if verdict == "deny":
            return Deny(reason=DENY_MESSAGE)
        return Confirm(prompt=prompt, reason=action.reason, response=answer)

    def _record(self, record: GuardRecord) -> None:
        with self._lock:
            self.records.append(record)
            if self.log_path:
                path = Path(self.log_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as f:
                    f.write(record.to_json() + "\n")


def user_request(messages: list[dict[str, Any]]) -> str:
    """The user's own text so far: user messages only, without tool results."""
    texts = [
        block["text"]
        for message in messages
        if message.get("role") == "user"
        for block in message.get("content", [])
        if isinstance(block, dict) and "text" in block
    ]
    return "\n".join(texts) if texts else "(no request)"


def confirmation_prompt(tool: str, arguments: dict[str, Any], carried: Iterable[str] = ()) -> str:
    """The question for the user, naming what would be sent, where, and which of their data.

    ``carried`` comes from the values, so a card number under ``order_note`` is still named.
    """
    carried = set(carried)
    labels = [label for name, label in FIELD_LABELS.items() if name in carried]
    labels += sorted(carried - FIELD_LABELS.keys())
    contains = f" It contains your {_join(labels)}." if labels else ""
    data, recipient = arguments.get("data"), arguments.get("recipient")
    if isinstance(data, dict) and recipient:
        fields = ", ".join(str(k) for k in data) or "nothing"
        return f"The assistant wants to send {fields} to {recipient}.{contains} Allow it?"
    call = json.dumps(arguments, default=str)
    return f"The assistant wants to call {tool} with {call}.{contains} Allow it?"


def _join(words: list[str]) -> str:
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} and {words[-1]}"


def _origin(url: str) -> str | None:
    """``scheme://host[:port]`` of an http(s) URL, lowercased, or None."""
    parts = urlsplit(url.strip())
    try:
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    return f"{parts.scheme.lower()}://{parts.hostname}" + (f":{port}" if port else "")


def _decision(
    subject_id: str, question: str, p: float, model: str, latency_ms: float | None
) -> Decision:
    return Decision(
        subject_kind="tool_call",
        subject_id=subject_id,
        question=question,
        question_type="noul",
        label="true" if p >= 0.5 else "false",
        confidence=abs(2 * p - 1),
        probabilities={"true": p, "false": 1 - p},
        model=model,
        latency_ms=latency_ms,
    )
