"""Builds the Strands agent: Bedrock generates, guardrails plug in as interventions."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from strands import Agent
from strands.interventions import InterventionHandler
from strands.models import BedrockModel, Model

from agent.config import Settings
from guardrails.tool_guard import ToolCallGuard

SYSTEM_PROMPT = (
    "You are a shopping assistant. You help the user find and compare products online. "
    "Only take actions the user asked for."
)


def build_agent(
    settings: Settings | None = None,
    *,
    model: Model | None = None,
    tools: Sequence[Any] = (),
    interventions: Sequence[InterventionHandler] = (),
) -> Agent:
    """Return an agent on the configured Bedrock model.

    `tools` come from tools/ and `interventions` from guardrails/. `model` replaces the Bedrock
    model, for tests that script the model's turns.
    """
    if model is None:
        settings = settings or Settings.from_env()
        model = BedrockModel(model_id=settings.model_id, region_name=settings.region)
    return Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=list(tools),
        interventions=list(interventions),
        callback_handler=None,
    )


def build_guarded_agent(
    settings: Settings | None = None,
    *,
    model: Model | None = None,
    tools: Sequence[Any] = (),
    guard: ToolCallGuard | None = None,
) -> Agent:
    """The agent with the tool-call guardrail: Decider checks sensitive calls before they run."""
    return build_agent(settings, model=model, tools=tools, interventions=[guard or ToolCallGuard()])
