"""
Tests for bucket_d_pipeline.py.

All unit tests inject a fake llm_fn — no real API calls. One live test
(opt-in via RUN_LIVE_API=1) uses the worked-example claim to verify the
full chain against real OpenAI.
"""

import os

import pytest

from bucket_d_pipeline import run_bucket_d_pipeline
from tag_schema import AssumptionsStatedEvidence, ClaimTag

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_CLAIM = (
    "Without TSMC, the global climate transition would be set back "
    "by a decade because advanced chips are essential for clean "
    "energy technology."
)
_COMPANY = "TSMC"
_CLAIM_ID = "tsmc-d-001"


def _make_llm_fn(assumptions=None, causal_steps=None):
    """Returns a well-formed llm_fn. Default: one stated assumption and one stated step."""
    if assumptions is None:
        assumptions = [
            {
                "text": "Advanced chips are essential for clean energy technology",
                "present_in_claim": True,
            },
        ]
    if causal_steps is None:
        causal_steps = [
            {
                "text": "No advanced chips → clean energy technology deployment slows",
                "present_in_claim": True,
            },
        ]

    _a = assumptions
    _cs = causal_steps

    def fn(claim_text, feedback):
        return {"assumptions": _a, "causal_steps": _cs}

    return fn


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


def test_returns_claim_tag_with_correct_bucket():
    """run_bucket_d_pipeline returns a ClaimTag with bucket='D'."""
    result = run_bucket_d_pipeline(
        _CLAIM,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        llm_fn=_make_llm_fn(),
    )
    assert isinstance(result, ClaimTag)
    assert result.bucket == "D"


def test_claim_id_and_claim_text_on_tag():
    """The returned ClaimTag carries the exact claim_id and claim_text passed in."""
    result = run_bucket_d_pipeline(
        _CLAIM,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        llm_fn=_make_llm_fn(),
    )
    assert result.claim_id == _CLAIM_ID
    assert result.claim_text == _CLAIM


def test_assumptions_evidence_populated():
    """assumptions_evidence is an AssumptionsStatedEvidence with real content."""
    result = run_bucket_d_pipeline(
        _CLAIM,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        llm_fn=_make_llm_fn(),
    )
    assert isinstance(result.assumptions_evidence, AssumptionsStatedEvidence)
    assert len(result.assumptions_evidence.assumptions) > 0
    assert len(result.assumptions_evidence.causal_steps) > 0


def test_stated_items_produce_assumptions_explicit():
    """At least one stated assumption and one stated step → 'assumptions_explicit'."""
    result = run_bucket_d_pipeline(
        _CLAIM,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        llm_fn=_make_llm_fn(
            assumptions=[{"text": "A stated premise", "present_in_claim": True}],
            causal_steps=[{"text": "A stated step", "present_in_claim": True}],
        ),
    )
    assert result.overall_status == "assumptions_explicit"


def test_all_unstated_produces_assumptions_not_stated():
    """All present_in_claim=False → 'assumptions_not_stated'."""
    result = run_bucket_d_pipeline(
        _CLAIM,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        llm_fn=_make_llm_fn(
            assumptions=[{"text": "Missing premise", "present_in_claim": False}],
            causal_steps=[{"text": "Inferential leap", "present_in_claim": False}],
        ),
    )
    assert result.overall_status == "assumptions_not_stated"


def test_malformed_llm_response_produces_assumptions_not_stated():
    """
    llm_fn always raises. analyze_assumptions surfaces this as empty lists;
    overall_status is 'assumptions_not_stated' and evidence lists are empty.
    """

    def raising_fn(claim_text, feedback):
        raise ValueError("simulated failure")

    result = run_bucket_d_pipeline(
        _CLAIM,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        llm_fn=raising_fn,
    )
    assert result.overall_status == "assumptions_not_stated"
    assert result.assumptions_evidence.assumptions == []
    assert result.assumptions_evidence.causal_steps == []


def test_llm_fn_is_called():
    """llm_fn is called at least once — confirms the wiring is not bypassed."""
    call_count = {"n": 0}

    def counting_fn(claim_text, feedback):
        call_count["n"] += 1
        return {
            "assumptions": [{"text": "A premise", "present_in_claim": True}],
            "causal_steps": [{"text": "A step", "present_in_claim": True}],
        }

    run_bucket_d_pipeline(
        _CLAIM,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        llm_fn=counting_fn,
    )
    assert call_count["n"] >= 1


def test_notes_field_is_empty_on_success():
    """On a normal run, notes is empty — the LLM never populates it."""
    result = run_bucket_d_pipeline(
        _CLAIM,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        llm_fn=_make_llm_fn(),
    )
    assert result.assumptions_evidence.notes == ""


# ---------------------------------------------------------------------------
# Live test (opt-in)
# ---------------------------------------------------------------------------


@pytest.mark.live_api
@pytest.mark.skipif(
    not os.getenv("RUN_LIVE_API"),
    reason="live API test; set RUN_LIVE_API=1 to run deliberately (costs money)",
)
def test_live_bucket_d_pipeline_tsmc_counterfactual():
    """
    No injected llm_fn. Real OpenAI call on the worked-example claim.
    Both "assumptions_explicit" and "assumptions_not_stated" are valid
    outcomes, but the worked example has explicit content so we assert
    non-empty lists.
    """
    result = run_bucket_d_pipeline(
        _CLAIM,
        company_name=_COMPANY,
        claim_id="tsmc-d-live-001",
    )

    assert isinstance(result, ClaimTag), f"Expected ClaimTag. Full result: {result!r}"
    assert result.bucket == "D", f"Expected bucket='D'. Full result: {result!r}"
    assert isinstance(
        result.assumptions_evidence, AssumptionsStatedEvidence
    ), f"Expected AssumptionsStatedEvidence. Full result: {result!r}"
    assert result.overall_status in (
        "assumptions_explicit",
        "assumptions_not_stated",
    ), f"Unexpected overall_status {result.overall_status!r}. Full result: {result!r}"
    assert len(result.assumptions_evidence.assumptions) >= 1, (
        f"Expected at least 1 assumption. Full result: "
        f"assumptions={result.assumptions_evidence.assumptions}, "
        f"causal_steps={result.assumptions_evidence.causal_steps}"
    )
    assert len(result.assumptions_evidence.causal_steps) >= 1, (
        f"Expected at least 1 causal step. Full result: "
        f"assumptions={result.assumptions_evidence.assumptions}, "
        f"causal_steps={result.assumptions_evidence.causal_steps}"
    )
