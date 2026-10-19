"""Builds the Strands agent: Bedrock generates, guardrails plug in as interventions."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from strands import Agent
from strands.interventions import InterventionHandler
from strands.models import BedrockModel

from agent.config import Settings

SYSTEM_PROMPT = (
    "You are a shopping assistant. You help the user find and compare products online. "
    "Only take actions the user asked for."
)


def build_agent(
    settings: Settings | None = None,
    *,
    tools: Sequence[Any] = (),
    interventions: Sequence[InterventionHandler] = (),
) -> Agent:
    """Return an agent on the configured Bedrock model.

    `tools` come from tools/ and `interventions` from guardrails/. Both are empty in the
    skeleton so the agent can be run before those pieces exist.
    """
    settings = settings or Settings.from_env()
    model = BedrockModel(model_id=settings.model_id, region_name=settings.region)
    return Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=list(tools),
        interventions=list(interventions),
        callback_handler=None,
    )
