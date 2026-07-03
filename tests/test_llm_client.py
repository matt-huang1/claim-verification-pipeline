"""
Tests for the shared LLM client abstraction.

The deterministic tests lock the wiring — model selection and the delegation
from default_complete_json to the process-wide client — with no network. The
live test confirms the real OpenAI-backed client returns parseable JSON.
"""

import json
import os

import pytest

from agent_eval import llm_client
from agent_eval.llm_client import OpenAIClient, default_complete_json


def test_explicit_model_overrides_env(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "from-env")
    assert OpenAIClient(model="explicit-model")._model == "explicit-model"


def test_falls_back_to_env_model(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "env-model")
    assert OpenAIClient()._model == "env-model"


def test_default_model_when_env_absent(monkeypatch):
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    assert OpenAIClient()._model == "gpt-5-nano"


def test_default_complete_json_delegates_to_the_process_client(monkeypatch):
    captured = {}

    class _Fake:
        def complete_json(self, system, user):
            captured["system"] = system
            captured["user"] = user
            return '{"ok": true}'

    monkeypatch.setattr(llm_client, "_default_client", _Fake())

    result = default_complete_json("SYS", "USR")

    assert result == '{"ok": true}'
    assert captured == {"system": "SYS", "user": "USR"}


@pytest.mark.live_api
@pytest.mark.skipif(
    not os.getenv("RUN_LIVE_API"),
    reason="live API test; set RUN_LIVE_API=1 to run deliberately (costs money)",
)
def test_live_openai_client_returns_parseable_json():
    raw = OpenAIClient().complete_json(
        'Respond with ONLY a JSON object: {"ok": true}.',
        "Return the object.",
    )
    assert isinstance(json.loads(raw), dict)
