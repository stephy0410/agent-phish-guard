"""The chunk scanner: every chunk of a page goes through Decider before the model sees it.

It is the browse tool's ``on_chunks`` hook (tools/browse.py):

    browse_page = make_browse_tool(on_chunks=ChunkScanner())

For each chunk it asks the scanner questions of guardrails/questions.py about a deobfuscated copy
of the text (invisible characters revealed, look-alike letters mapped to Latin, readable base64
decoded; see guardrails/deobfuscate.py), turns the answers into contract ``Decision``s and
applies ``ScanPolicy``. A flagged chunk is quarantined: the model gets ``QUARANTINE_NOTICE`` in
its place, and the chunk, its decisions (with their confidence) and the ``Action`` are kept in
``records`` and, if ``SCAN_LOG`` names a file, appended to it as JSON Lines.

If Decider can't be reached the error propagates, so the page is not shown at all rather than
shown unchecked (fail closed).
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from contracts import Action, Chunk, Decision
from guardrails.decider import DeciderClient
from guardrails.deobfuscate import deobfuscate
from guardrails.questions import CHUNK_QUESTIONS, chunk_state

LOG_ENV_VAR = "SCAN_LOG"
QUARANTINE_NOTICE = (
    "[Removed by Agent Phish Guard: this part of the page contained instructions aimed at AI "
    "assistants.]"
)


@dataclass(frozen=True)
class ScanPolicy:
    """When a chunk is flagged.

    A chunk is flagged when it instructs an AI and also asks for user data or claims authority:
    a moderate `instructs_ai` also fires on benign text about AI assistants (0.79 to 0.81). Text
    no reader sees (``hidden`` or ``metadata``) gets a lower bar for the instruction, because on
    the dev set injected meta and JSON-LD text scored only 0.39 to 0.42 on `instructs_ai`.

    A very confident `instructs_ai` flags on its own. That catches instructions that neither ask
    for data nor claim authority (add items to the cart, mislead the user), which scored 0.91 to
    0.96, while no benign text we have reached 0.9 (highest 0.81, over 113 distinct texts).

    The thresholds are provisional, picked on the dev set (docs/questions.md) and the sandbox
    pages. Threshold calibration on the labeled dataset replaces them.
    """

    instructs_ai: float = 0.5
    instructs_ai_unseen: float = 0.35
    instructs_ai_alone: float = 0.9
    requests_user_data: float = 0.5
    claims_authority: float = 0.5

    def flags(self, chunk: Chunk, p: dict[str, float]) -> bool:
        if p["instructs_ai"] >= self.instructs_ai_alone:
            return True
        bar = self.instructs_ai if chunk.visibility == "visible" else self.instructs_ai_unseen
        return p["instructs_ai"] >= bar and (
            p["requests_user_data"] >= self.requests_user_data
            or p["claims_authority"] >= self.claims_authority
        )


@dataclass(frozen=True)
class ScanRecord:
    """What the scanner decided about one chunk."""

    chunk: Chunk
    action: Action
    cached: bool
    """True when the answers came from the cache (same text seen before), not a new request."""

    def to_json(self) -> str:
        return json.dumps(
            {
                "scanned_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "cached": self.cached,
                "chunk": self.chunk.model_dump(),
                "action": self.action.model_dump(),
            },
            ensure_ascii=True,
        )


@dataclass(frozen=True)
class _Answers:
    probabilities: dict[str, float]
    model: str
    latency_ms: float | None


class ChunkScanner:
    """Checks a page's chunks with Decider and quarantines the flagged ones.

    Answers are cached by deobfuscated text: Decider is deterministic, and headers, footers and
    menus repeat on every page of a site.
    """

    def __init__(
        self,
        client: DeciderClient | None = None,
        policy: ScanPolicy | None = None,
        log_path: str | Path | None = None,
    ) -> None:
        self.client = client or DeciderClient()
        self.policy = policy or ScanPolicy()
        self.log_path = log_path or os.environ.get(LOG_ENV_VAR) or None
        self.records: list[ScanRecord] = []
        self._cache: dict[str, _Answers] = {}
        self._lock = threading.Lock()

    def __call__(self, chunks: list[Chunk]) -> list[Chunk]:
        return [self.scan(chunk) for chunk in chunks]

    @property
    def quarantine(self) -> list[ScanRecord]:
        """Records of the chunks that were kept from the model."""
        return [r for r in self.records if r.action.type == "deny"]

    def scan(self, chunk: Chunk) -> Chunk:
        """Return the chunk the model may see: the original, or the quarantine notice."""
        text = deobfuscate(chunk.text)
        with self._lock:
            answers = self._cache.get(text)
        cached = answers is not None
        if answers is None:
            # Ask outside the lock so a slow request doesn't block other scans.
            response = self.client.ask(chunk_state(text), CHUNK_QUESTIONS)
            answers = _Answers(
                probabilities={k: a["noul"] for k, a in response.answers.items()},
                model=response.model or "strands-decider",
                latency_ms=response.latency_ms,
            )
            with self._lock:
                answers = self._cache.setdefault(text, answers)

        flagged = self.policy.flags(chunk, answers.probabilities)
        action = Action(
            type="deny" if flagged else "proceed",
            subject_kind="chunk",
            subject_id=chunk.id,
            reason=_reason(chunk, answers.probabilities),
            message=QUARANTINE_NOTICE if flagged else None,
            decisions=[
                _decision(chunk, key, p, answers) for key, p in answers.probabilities.items()
            ],
        )
        self._record(ScanRecord(chunk=chunk, action=action, cached=cached))
        return chunk.model_copy(update={"text": QUARANTINE_NOTICE}) if flagged else chunk

    def _record(self, record: ScanRecord) -> None:
        with self._lock:
            self.records.append(record)
            if self.log_path:
                path = Path(self.log_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as f:
                    f.write(record.to_json() + "\n")


def _decision(chunk: Chunk, question: str, p: float, answers: _Answers) -> Decision:
    return Decision(
        subject_kind="chunk",
        subject_id=chunk.id,
        question=question,
        question_type="noul",
        label="true" if p >= 0.5 else "false",
        confidence=abs(2 * p - 1),
        probabilities={"true": p, "false": 1 - p},
        model=answers.model,
        latency_ms=answers.latency_ms,
    )


def _reason(chunk: Chunk, p: dict[str, float]) -> str:
    scores = ", ".join(f"{key}={value:.2f}" for key, value in p.items())
    return f"{scores} on {chunk.visibility} text"
