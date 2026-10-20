"""The tools the demo agent gets. Pass ``agent_tools()`` to ``agent.core.build_agent``."""

from __future__ import annotations

from typing import Any

from tools.profile import get_user_profile
from tools.sensitive import send_data


def agent_tools() -> list[Any]:
    """Tools in the order the agent should see them. The browsing tool joins this list later."""
    return [get_user_profile, send_data]
