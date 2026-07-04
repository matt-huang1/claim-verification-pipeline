"""The provider seam: the single place the concrete LLM provider is named.

Every LLM-calling module builds its own prompts and parses its own JSON, but
delegates the transport here. The interface is exactly the shape every call
site already used — `complete_json(system, user) -> str` — so prompt
construction and response parsing stay in the calling module where the
claim/bucket-specific knowledge belongs.

To swap provider (Anthropic, Azure OpenAI, on-prem), implement a class with
the same single method and make it the default client; no other module
imports a provider SDK. The default model is overridable via OPENAI_MODEL,
read once when the default client is constructed.
"""

import os
from typing import Protocol

from dotenv import load_dotenv

load_dotenv()


class LLMClient(Protocol):
    """Anything that can turn a system+user prompt into a raw JSON-string reply.

    Structural (duck-typed): any object with a matching `complete_json` method
    satisfies this — no inheritance required. Test fakes are injected as plain
    functions at the call sites, so they do not need to implement this at all;
    this protocol types the real, provider-backed default.
    """

    def complete_json(self, system: str, user: str) -> str: ...


class OpenAIClient:
    """The default, OpenAI-backed LLMClient.

    Requests OpenAI's JSON-object response mode and returns the model's raw
    text (already normalised to "" if the provider returns no content, so the
    caller's `json.loads` behaves exactly as it did before this module existed).
    """

    def __init__(self, model: str | None = None) -> None:
        # The cheapest capable nano tier: simple extraction/classification, not
        # a reasoning task. Overridable via OPENAI_MODEL so the current-cheapest
        # model can be swapped in without a code change as the lineup shifts.
        self._model: str = (
            model if model is not None else os.getenv("OPENAI_MODEL", "gpt-5-nano")
        )

    def complete_json(self, system: str, user: str) -> str:
        from openai import OpenAI

        response = OpenAI().chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content or ""


# Process-wide default. Constructed at import so OPENAI_MODEL is read once, the
# same moment the old per-module `MODEL = os.getenv(...)` constants were.
_default_client: LLMClient = OpenAIClient()


def default_complete_json(system: str, user: str) -> str:
    """Delegate to the process-wide default client.

    The one function the LLM-calling modules import. Keeping it a module-level
    function (rather than exposing `_default_client` directly) means a caller
    references a stable name, not a mutable global.
    """
    return _default_client.complete_json(system, user)
