"""Ask the agent one question from the command line.

uv run python -m agent "Find me the cheapest leggings"
"""

from __future__ import annotations

import sys

from agent.core import build_agent

DEFAULT_QUESTION = "In one sentence, what is indirect prompt injection?"


def main(argv: list[str]) -> int:
    question = " ".join(argv) or DEFAULT_QUESTION
    print(build_agent()(question))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
