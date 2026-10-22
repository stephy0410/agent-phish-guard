"""The tools the demo agent gets. Pass ``agent_tools()`` to ``agent.core.build_agent``."""

from __future__ import annotations

from typing import Any

from tools.browse import browse_page
from tools.profile import get_user_profile
from tools.sensitive import send_data


def agent_tools() -> list[Any]:
    """Tools in the order the agent should see them, unguarded (``agent.demo`` adds guards)."""
    return [browse_page, get_user_profile, send_data]
