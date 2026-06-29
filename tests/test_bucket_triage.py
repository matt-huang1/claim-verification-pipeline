"""
Tests for bucket_triage.py.

All unit tests inject a fake llm_fn — no real API calls are made.
One live test (opt-in via RUN_LIVE_API=1) calls triage_claim against the two
real counterexample claims the module's design is built around.
"""

import os

import pytest

from bucket_triage import triage_claim

# ---------------------------------------------------------------------------
# Unit tests — all use a mocked llm_fn
# ---------------------------------------------------------------------------


def test_bucket_a_passthrough():
    """A bucket_a classification with reasoning is returned as-is."""
    result = triage_claim(
        "some claim",
        llm_fn=lambda _: {
            "classification": "bucket_a",
            "reasoning": "precisely bounded category",
        },
    )
    assert result["classification"] == "bucket_a"
    assert result["reasoning"] == "precisely bounded category"


def test_bucket_c_passthrough():
    """A bucket_c classification with reasoning is returned as-is."""
    result = triage_claim(
        "some claim",
        llm_fn=lambda _: {
            "classification": "bucket_c",
            "reasoning": "definitionally contested market boundary",
        },
    )
    assert result["classification"] == "bucket_c"
    assert result["reasoning"] == "definitionally contested market boundary"


def test_ambiguous_passthrough_no_retry():
    """
    An ambiguous classification is returned as-is. The function body has no
    retry loop, so the llm_fn is called exactly once — confirmed by the call
    counter below.
    """
    call_count = {"n": 0}

    def counting_fn(_):
        call_count["n"] += 1
        return {"classification": "ambiguous", "reasoning": "cannot determine boundary"}

    result = triage_claim("some claim", llm_fn=counting_fn)
    assert result["classification"] == "ambiguous"
    assert result["reasoning"] == "cannot determine boundary"
    assert call_count["n"] == 1, "ambiguous must not trigger any retry"


def test_malformed_json_yields_malformed_llm_response():
    """A llm_fn that raises (simulating bad JSON) yields malformed_llm_response."""

    def bad_fn(_):
        raise ValueError("simulated JSON decode error")

    result = triage_claim("some claim", llm_fn=bad_fn)
    assert result["classification"] == "malformed_llm_response"
    assert result["reasoning"] is None


def test_invalid_classification_value_yields_malformed_llm_response():
    """
    A classification value outside the three allowed strings must be caught
    as malformed_llm_response, not silently accepted as a fourth outcome.
    """
    result = triage_claim(
        "some claim",
        llm_fn=lambda _: {
            "classification": "bucket_b",
            "reasoning": "this should not be accepted",
        },
    )
    assert result["classification"] == "malformed_llm_response"
    assert result["reasoning"] is None


def test_missing_reasoning_field_yields_malformed_llm_response():
    """
    A response missing the 'reasoning' field must be treated as malformed.
    Reasoning is required on every real outcome, not optional.
    """
    result = triage_claim(
        "some claim",
        llm_fn=lambda _: {"classification": "bucket_a"},
    )
    assert result["classification"] == "malformed_llm_response"
    assert result["reasoning"] is None


def test_missing_classification_field_yields_malformed_llm_response():
    """A response missing the 'classification' field is malformed."""
    result = triage_claim(
        "some claim",
        llm_fn=lambda _: {"reasoning": "no classification key present"},
    )
    assert result["classification"] == "malformed_llm_response"
    assert result["reasoning"] is None


# ---------------------------------------------------------------------------
# Live test — opt-in only, costs money
# ---------------------------------------------------------------------------


@pytest.mark.live_api
@pytest.mark.skipif(
    not os.getenv("RUN_LIVE_API"),
    reason="live API test; set RUN_LIVE_API=1 to run deliberately (costs money)",
)
def test_live_triage_counterexample_claims():
    """
    Calls triage_claim with no injected llm_fn (real OpenAI call) against the
    two worked counterexamples from the module docstring:

      - "TSMC is the world's largest pure-play foundry by revenue"
        Expected: bucket_a — "pure-play foundry" is precisely bounded.

      - "TSMC has roughly 60% of the foundry market"
        Expected: bucket_c — "the foundry market" is definitionally contested.

    This test proves the system prompt's worked-example design produces the
    correct real-world classification on the exact claims the module design
    is built around, not just that the function plumbing works.
    Requires OPENAI_API_KEY in the environment.
    """
    bucket_a_claim = "TSMC is the world's largest pure-play foundry by revenue"
    bucket_c_claim = "TSMC has roughly 60% of the foundry market"

    result_a = triage_claim(bucket_a_claim)
    assert result_a["classification"] == "bucket_a", (
        f"Expected bucket_a for '{bucket_a_claim}', "
        f"got {result_a['classification']!r}. Reasoning: {result_a['reasoning']}"
    )
    assert isinstance(result_a["reasoning"], str) and result_a["reasoning"]

    result_c = triage_claim(bucket_c_claim)
    assert result_c["classification"] == "bucket_c", (
        f"Expected bucket_c for '{bucket_c_claim}', "
        f"got {result_c['classification']!r}. Reasoning: {result_c['reasoning']}"
    )
    assert isinstance(result_c["reasoning"], str) and result_c["reasoning"]
