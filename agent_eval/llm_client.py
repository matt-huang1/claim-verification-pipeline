"""
llm_client.py

The single place the concrete LLM provider is named. Every LLM-calling module
in this package (extraction, bucket_triage, criterion_evidence, source_extraction,
reconciliation, bucket_b_pipeline, bucket_d_analysis) builds its own prompts and
parses its own JSON, but delegates the actual API call here.

WHY THIS EXISTS:

Before this module, the same transport block — `from openai import OpenAI;
client.chat.completions.create(model=..., messages=[system, user],
response_format={"type": "json_object"}); json.loads(...)` — was copy-pasted into
seven call sites across six modules, and each independently hard-coded the model
default (`gpt-5-nano`). Swapping provider or model meant editing seven places and
hoping they matched. Concentrating the provider behind one small interface makes
that a one-file change: the pipeline depends on the CAPABILITY (turn a
system+user prompt into a JSON string), not on OpenAI specifically.

WHY THE INTERFACE IS `complete_json(system, user) -> str`:

Every existing call site used exactly this shape — a system prompt, a single user
message, and OpenAI's JSON-object response mode — and each then ran
`json.loads()` on the returned text. So the shared, provider-specific part is
precisely "given two strings, return the model's raw text reply." Prompt
construction and response parsing stay in the calling module, where the
claim/bucket-specific knowledge belongs; only the transport is centralised.

SWAPPING PROVIDER:

To run against Anthropic, Azure OpenAI, or an on-prem model, implement a class
with the same single `complete_json` method and make it the default client. No
other module changes, because none of them import a provider SDK directly.

The default provider and model are still overridable at runtime via the
OPENAI_MODEL environment variable, read once when the default client is
constructed — the same timing as the module-level constants this replaced.
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
