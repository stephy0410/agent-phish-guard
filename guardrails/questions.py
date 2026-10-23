"""Moved to ``phish_guard.questions``; this keeps ``guardrails.questions`` imports and the
``python -m guardrails.questions`` command working."""

from phish_guard.questions import *  # noqa: F403
from phish_guard.questions import main

if __name__ == "__main__":
    raise SystemExit(main())
