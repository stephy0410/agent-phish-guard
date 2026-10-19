import os

import pytest

from agent.config import DEFAULT_MODEL_ID, DEFAULT_REGION, Settings
from agent.core import build_agent


def test_settings_default_without_env(monkeypatch):
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    assert Settings.from_env() == Settings(region=DEFAULT_REGION, model_id=DEFAULT_MODEL_ID)


def test_settings_read_env(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "some-model")
    assert Settings.from_env() == Settings(region="us-west-2", model_id="some-model")


def test_build_agent_uses_settings():
    agent = build_agent(Settings(region="us-west-2", model_id="some-model"))
    assert agent.model.get_config()["model_id"] == "some-model"
    assert agent.model.client.meta.region_name == "us-west-2"


@pytest.mark.bedrock
@pytest.mark.skipif(not os.environ.get("RUN_BEDROCK_TESTS"), reason="set RUN_BEDROCK_TESTS=1")
def test_agent_answers_with_bedrock():
    result = build_agent()("Reply with the single word: pong")
    assert "pong" in str(result).lower()
