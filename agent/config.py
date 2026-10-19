"""Runtime settings, read from the environment (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_REGION = "us-east-1"
DEFAULT_MODEL_ID = "us.anthropic.claude-opus-5-5"


@dataclass(frozen=True)
class Settings:
    region: str = DEFAULT_REGION
    model_id: str = DEFAULT_MODEL_ID

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            region=os.environ.get("AWS_REGION", DEFAULT_REGION),
            model_id=os.environ.get("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID),
        )
