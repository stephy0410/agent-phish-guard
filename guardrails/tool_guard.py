"""This demo's tool-call guard: ``phish_guard.tool_guard`` set up for the Lumora sandbox.

The library knows nothing about this repo. Here the guard gets the two things only the demo knows:
how to find the fake user's data in a call (``find_leaked_fields`` of tools/sensitive.py) and which
recipients count as given (the fake user's email and the sandbox site). Everything else is
re-exported unchanged, so ``guardrails.tool_guard`` imports keep working.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from phish_guard import tool_guard as _library
from phish_guard.tool_guard import *  # noqa: F403  (the rest of the public API, unchanged)
from tools.browse import BASE_URL, LOCAL_HOSTS
from tools.profile import FAKE_PROFILE
from tools.sensitive import find_leaked_fields

SITE_ORIGINS = frozenset(f"http://{host}:{urlsplit(BASE_URL).port}" for host in LOCAL_HOSTS)
"""The sandbox site the browse tool opens. Its own URLs (a checkout) may receive data."""


@dataclass(frozen=True)
class RecipientRule(_library.RecipientRule):
    """The library's rule, with the sandbox site and the fake user's email as given recipients."""

    site_origins: frozenset[str] = SITE_ORIGINS
    own_addresses: frozenset[str] = frozenset({FAKE_PROFILE["email"]})


DEMO_RULE = RecipientRule()


class ToolCallGuard(_library.ToolCallGuard):
    """The library's guard with the demo's ``find_user_data`` and ``RecipientRule`` on by default.

    ``recipient_rule=None`` still turns the rule off, to measure Decider alone.
    """

    def __init__(
        self,
        *args: Any,
        find_user_data: _library.FindUserData | None = find_leaked_fields,
        recipient_rule: _library.RecipientRule | None = DEMO_RULE,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            *args, find_user_data=find_user_data, recipient_rule=recipient_rule, **kwargs
        )
