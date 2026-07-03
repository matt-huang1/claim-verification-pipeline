"""
Tests for bucket_triage.py.

All unit tests inject a fake llm_fn — no real API calls are made.
One live test (opt-in via RUN_LIVE_API=1) calls triage_claim against the two
real counterexample claims the module's design is built around.
"""

import os

import pytest

from agent_eval.bucket_triage import triage_claim

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
    A classification value outside the four allowed strings must be caught
    as malformed_llm_response, not silently accepted.
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


def test_bucket_d_passthrough():
    """A bucket_d classification with reasoning is returned as-is."""
    result = triage_claim(
        "some counterfactual claim",
        llm_fn=lambda _: {
            "classification": "bucket_d",
            "reasoning": "counterfactual — no source can verify this",
        },
    )
    assert result["classification"] == "bucket_d"
    assert result["reasoning"] == "counterfactual — no source can verify this"


def test_bucket_d_not_retried():
    """
    bucket_d is a stable judgment outcome — llm_fn is called exactly once,
    same as ambiguous.
    """
    call_count = {"n": 0}

    def counting_fn(_):
        call_count["n"] += 1
        return {
            "classification": "bucket_d",
            "reasoning": "uncheckable counterfactual",
        }

    result = triage_claim("some claim", llm_fn=counting_fn)
    assert result["classification"] == "bucket_d"
    assert call_count["n"] == 1, "bucket_d must not trigger any retry"


def test_bucket_d_invalid_string_still_yields_malformed():
    """
    bucket_b remains invalid after the extension — only bucket_a, bucket_c,
    bucket_d, and ambiguous are accepted.
    """
    result = triage_claim(
        "some claim",
        llm_fn=lambda _: {
            "classification": "bucket_b",
            "reasoning": "should still be rejected",
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

      - "TSMC's revenue was $69.3 billion in FY2023."
        Expected: bucket_a — a specific historical figure with one
        authoritative source and no definitional contest.
      - "TSMC has roughly 60% of the foundry market"
        Expected: bucket_c — "the foundry market" is definitionally contested.

    This test proves the system prompt's worked-example design produces the
    correct real-world classification on the exact claims the module design
    is built around, not just that the function plumbing works.
    Requires OPENAI_API_KEY in the environment.
    """
    bucket_a_claim = "TSMC's revenue was $69.3 billion in FY2023."
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

    bucket_d_claim = (
        "Without TSMC, the climate transition would be set back by a decade."
    )
    result_d = triage_claim(bucket_d_claim)
    assert result_d["classification"] == "bucket_d", (
        f"Expected bucket_d for '{bucket_d_claim}', "
        f"got {result_d['classification']!r}. "
        f"Reasoning: {result_d['reasoning']}"
    )
    assert isinstance(result_d["reasoning"], str) and result_d["reasoning"]

    bucket_a_commitment_claim = "TSMC committed to achieving RE100 by 2040."
    result_a2 = triage_claim(bucket_a_commitment_claim)
    assert result_a2["classification"] == "bucket_a", (
        f"Expected bucket_a for '{bucket_a_commitment_claim}', "
        f"got {result_a2['classification']!r}. "
        f"Reasoning: {result_a2['reasoning']}"
    )
    assert isinstance(result_a2["reasoning"], str) and result_a2["reasoning"]
