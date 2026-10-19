"""Minimal HTTP client for a running Strands Decider server (POST /v1/systemone).

Start a server first:

    strands-decider serve StrandsAgents/strands-decider-2B-qwen3.5-v1-2610 --port 8099

Uses urllib so the agent does not depend on torch: the model lives in the server process.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

DEFAULT_URL = "http://127.0.0.1:8099"
SERVE_HINT = "strands-decider serve StrandsAgents/strands-decider-2B-qwen3.5-v1-2610 --port 8099"


class DeciderUnavailable(RuntimeError):
    """Nothing is answering at the Decider URL."""


@dataclass(frozen=True)
class DeciderResponse:
    answers: dict[str, Any]
    input_tokens: int
    latency_ms: float | None
    """Server-side latency as reported by the server, excluding HTTP overhead."""


class DeciderClient:
    """One state, many typed questions, one round trip.

    The state is encoded once and shared across the questions, so ask everything about a
    piece of text in a single call.
    """

    def __init__(self, url: str | None = None, timeout: float = 120.0) -> None:
        self.url = (url or os.environ.get("DECIDER_URL", DEFAULT_URL)).rstrip("/")
        # Generous: on Apple silicon the first request at a new input length pays a one-off
        # shape compile that can take seconds.
        self.timeout = timeout

    @staticmethod
    def noul(instructions: str, criteria: Mapping[str, str] | None = None) -> dict[str, Any]:
        """Yes/no question. The answer is P(true); `criteria` describes "true" and "false"."""
        question: dict[str, Any] = {"type": "noul", "instructions": instructions}
        if criteria:
            question["criteria"] = dict(criteria)
        return question

    @staticmethod
    def choice(instructions: str, criteria: Mapping[str, str]) -> dict[str, Any]:
        """Pick one of N options. `criteria` maps option name to what it means."""
        return {"type": "choice", "instructions": instructions, "criteria": dict(criteria)}

    @staticmethod
    def score(instructions: str, levels: list[str]) -> dict[str, Any]:
        """Rate against an ordered rubric, lowest level first."""
        return {"type": "score", "instructions": instructions, "criteria": list(levels)}

    def ask(self, state: Any, questions: Mapping[str, dict[str, Any]]) -> DeciderResponse:
        body = json.dumps({"state": state, "questions": dict(questions)}).encode()
        request = urllib.request.Request(
            f"{self.url}/v1/systemone", data=body, headers={"content-type": "application/json"}
        )
        payload = self._send(request)
        return DeciderResponse(
            answers=payload["answers"],
            input_tokens=payload.get("usage", {}).get("input_tokens", 0),
            latency_ms=payload.get("latency_ms"),
        )

    def health(self) -> dict[str, Any]:
        """The loaded checkpoint, device and context window."""
        return self._send(urllib.request.Request(f"{self.url}/health"))

    def _send(self, request: urllib.request.Request) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.URLError as exc:
            raise DeciderUnavailable(
                f"no Decider server at {self.url} ({exc}). Start one with:\n    {SERVE_HINT}"
            ) from exc
