"""
Tests for bucket_c_pipeline.py.

All unit tests inject fakes for every LLM and network call — no real API calls.

Test organisation:
  - triage routing: bucket_a, ambiguous, malformed_llm_response
  - successful completion: completed outcome, ClaimTag shape, overall_status
  - injectable fake plumbing: all fakes called, gather skipped on early exit
  - live API smoke test (opt-in via RUN_LIVE_API=1)
"""

import os

import pytest

from bucket_c_pipeline import run_bucket_c_pipeline
from tag_schema import ClaimTag, SourcePluralityEvidence

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_CLAIM = "TSMC has roughly 60% of the foundry market"
_ALLOWLIST = ["tsmc.com"]
_COMPANY = "TSMC"
_CLAIM_ID = "tsmc-c-001"

_FAKE_URL_1 = "https://icinsights.com/tsmc-market-share"
_FAKE_URL_2 = "https://trendforce.com/foundry-market"

_FAKE_SEARCH_RESULTS = [
    {"url": _FAKE_URL_1, "title": "IC Insights: TSMC", "snippet": "TSMC foundry"},
    {"url": _FAKE_URL_2, "title": "TrendForce: Foundry", "snippet": "foundry market"},
]

_FAKE_DOCUMENT = (
    "TSMC held approximately 60% of the global pure-play foundry market "
    "by revenue in 2023, according to IC Insights data. The pure-play "
    "foundry market excludes in-house IDM fabrication capacity."
)

_FAKE_FETCH_RESULT = {
    "success": True,
    "text": _FAKE_DOCUMENT,
    "content_type": "text/html",
    "failure_reason": None,
}

# ---------------------------------------------------------------------------
# Shared fake helpers
# ---------------------------------------------------------------------------


def _make_triage_fn(classification, reasoning="some reasoning"):
    def fn(claim_text):
        return {"classification": classification, "reasoning": reasoning}

    return fn


def _make_search_fn(results=None):
    if results is None:
        results = _FAKE_SEARCH_RESULTS

    def fn(query):
        return results

    return fn


def _make_url_llm_fn(url=_FAKE_URL_1):
    def fn(claim_text, search_results):
        return {"url": url}

    return fn


def _make_fetch_fn(result=None):
    if result is None:
        result = _FAKE_FETCH_RESULT

    def fn(url):
        return result

    return fn


def _make_finding_llm_fn():
    """
    Returns a finding_llm_fn that reports both value and definition found,
    using substrings verbatim from _FAKE_DOCUMENT so quote_match passes.
    """

    def fn(document, claim_text):
        return {
            "value_found": True,
            "claimed_value": "approximately 60% of the global pure-play foundry market",
            "is_literal_value": True,
            "definition_found": True,
            "definition_text": (
                "pure-play foundry market excludes in-house IDM fabrication capacity"
            ),
        }

    return fn


def _make_reconciliation_fn(groups=None, distinct=None, unresolved=None):
    """
    Returns a reconciliation_llm_fn. Default: one group with both fake URLs
    (the two sources both found and grouped).
    """
    if groups is None:
        groups = [
            {
                "member_source_urls": [_FAKE_URL_1, _FAKE_URL_2],
                "shared_definition_label": "pure-play foundry market",
                "reasoning": "Both exclude IDM in-house capacity.",
            }
        ]
    if distinct is None:
        distinct = []
    if unresolved is None:
        unresolved = []

    _groups = groups
    _distinct = distinct
    _unresolved = unresolved

    def fn(claim_text, findings, feedback):
        return {"groups": _groups, "distinct": _distinct, "unresolved": _unresolved}

    return fn


# ---------------------------------------------------------------------------
# Triage routing tests
# ---------------------------------------------------------------------------


def test_triage_bucket_a_returns_routed_dict():
    """
    When triage returns "bucket_a", outcome is "routed_to_bucket_a",
    triage_reasoning is populated, tag is None, and gather/reconcile are
    never called.
    """
    gather_called = {"n": 0}
    reconcile_called = {"n": 0}

    def counting_search_fn(query):
        gather_called["n"] += 1
        return []

    def counting_reconciliation_fn(claim_text, findings, feedback):
        reconcile_called["n"] += 1
        return {"groups": [], "distinct": [], "unresolved": []}

    result = run_bucket_c_pipeline(
        _CLAIM,
        _ALLOWLIST,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        triage_llm_fn=_make_triage_fn("bucket_a", "single authoritative source"),
        search_fn=counting_search_fn,
        reconciliation_llm_fn=counting_reconciliation_fn,
    )

    assert result["outcome"] == "routed_to_bucket_a"
    assert result["triage_reasoning"] == "single authoritative source"
    assert result["tag"] is None
    assert gather_called["n"] == 0
    assert reconcile_called["n"] == 0


def test_triage_ambiguous_returns_ambiguous_dict():
    """
    When triage returns "ambiguous", outcome is "ambiguous", triage_reasoning
    is populated, tag is None, and gather/reconcile are never called.
    """
    gather_called = {"n": 0}

    def counting_search_fn(query):
        gather_called["n"] += 1
        return []

    result = run_bucket_c_pipeline(
        _CLAIM,
        _ALLOWLIST,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        triage_llm_fn=_make_triage_fn("ambiguous", "cannot confidently classify"),
        search_fn=counting_search_fn,
    )

    assert result["outcome"] == "ambiguous"
    assert result["triage_reasoning"] == "cannot confidently classify"
    assert result["tag"] is None
    assert gather_called["n"] == 0


def test_triage_malformed_returns_triage_failed_dict():
    """
    When the triage LLM returns a malformed response, outcome is
    "triage_failed", triage_reasoning is None, tag is None, and
    gather/reconcile are never called.
    """
    gather_called = {"n": 0}

    def counting_search_fn(query):
        gather_called["n"] += 1
        return []

    def bad_triage_fn(claim_text):
        return {"not_a_valid_field": "garbage"}

    result = run_bucket_c_pipeline(
        _CLAIM,
        _ALLOWLIST,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        triage_llm_fn=bad_triage_fn,
        search_fn=counting_search_fn,
    )

    assert result["outcome"] == "triage_failed"
    assert result["triage_reasoning"] is None
    assert result["tag"] is None
    assert gather_called["n"] == 0


# ---------------------------------------------------------------------------
# Successful completion tests
# ---------------------------------------------------------------------------


def test_completed_outcome_returns_claim_tag():
    """
    Full pipeline with all fakes injected. Assert outcome="completed",
    tag is a ClaimTag with bucket="C", source_plurality_evidence is a
    SourcePluralityEvidence, overall_status is one of the two valid
    Bucket C outcomes.
    """
    result = run_bucket_c_pipeline(
        _CLAIM,
        _ALLOWLIST,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        triage_llm_fn=_make_triage_fn("bucket_c"),
        search_fn=_make_search_fn(),
        url_llm_fn=_make_url_llm_fn(),
        fetch_fn=_make_fetch_fn(),
        finding_llm_fn=_make_finding_llm_fn(),
        reconciliation_llm_fn=_make_reconciliation_fn(),
    )

    assert result["outcome"] == "completed"
    assert isinstance(result["tag"], ClaimTag)
    assert result["tag"].bucket == "C"
    assert isinstance(result["tag"].source_plurality_evidence, SourcePluralityEvidence)
    assert result["tag"].overall_status in (
        "disambiguated",
        "definitional_ambiguity_unresolved",
    )


def test_completed_with_real_group_is_disambiguated():
    """
    When reconciliation returns a group with 2 members, overall_status is
    "disambiguated".

    url_llm_fn alternates between two URLs so gather produces two distinct
    source findings, giving reconciliation two candidates to group.
    """
    url_call_count = {"n": 0}

    def alternating_url_llm_fn(claim_text, search_results):
        url_call_count["n"] += 1
        return {"url": _FAKE_URL_1 if url_call_count["n"] % 2 == 1 else _FAKE_URL_2}

    result = run_bucket_c_pipeline(
        _CLAIM,
        _ALLOWLIST,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        triage_llm_fn=_make_triage_fn("bucket_c"),
        search_fn=_make_search_fn(),
        url_llm_fn=alternating_url_llm_fn,
        fetch_fn=_make_fetch_fn(),
        finding_llm_fn=_make_finding_llm_fn(),
        reconciliation_llm_fn=_make_reconciliation_fn(
            groups=[
                {
                    "member_source_urls": [_FAKE_URL_1, _FAKE_URL_2],
                    "shared_definition_label": "pure-play foundry",
                    "reasoning": "Same scope.",
                }
            ],
            distinct=[],
            unresolved=[],
        ),
    )

    assert result["outcome"] == "completed"
    assert result["tag"].overall_status == "disambiguated"


def test_completed_with_no_group_is_definitional_ambiguity_unresolved():
    """
    When reconciliation returns all sources as unresolved (no groups),
    overall_status is "definitional_ambiguity_unresolved".
    """
    result = run_bucket_c_pipeline(
        _CLAIM,
        _ALLOWLIST,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        triage_llm_fn=_make_triage_fn("bucket_c"),
        search_fn=_make_search_fn(),
        url_llm_fn=_make_url_llm_fn(),
        fetch_fn=_make_fetch_fn(),
        finding_llm_fn=_make_finding_llm_fn(),
        reconciliation_llm_fn=_make_reconciliation_fn(
            groups=[],
            distinct=[],
            unresolved=[
                {"source_url": _FAKE_URL_1, "reasoning": "Too vague."},
                {"source_url": _FAKE_URL_2, "reasoning": "Unclear scope."},
            ],
        ),
    )

    assert result["outcome"] == "completed"
    assert result["tag"].overall_status == "definitional_ambiguity_unresolved"


def test_empty_findings_still_completes():
    """
    When gather returns [], reconcile_sources is still called with an empty list.
    The pipeline runs to completion (no short-circuit) and the tag is the
    honest record of what happened.

    With 0 findings, reconcile_sources takes its deterministic 0-candidate path
    (no LLM call), so reconciliation_llm_fn is not called. We verify pipeline
    completion via the outcome and overall_status, not via LLM call count.
    """
    result = run_bucket_c_pipeline(
        _CLAIM,
        _ALLOWLIST,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        triage_llm_fn=_make_triage_fn("bucket_c"),
        search_fn=_make_search_fn(results=[]),  # gather returns []
    )

    assert result["outcome"] == "completed"
    assert result["tag"].overall_status == "definitional_ambiguity_unresolved"


def test_claim_id_and_claim_text_on_tag():
    """
    The returned ClaimTag carries the exact claim_id and claim_text passed
    to the orchestrator.
    """
    result = run_bucket_c_pipeline(
        _CLAIM,
        _ALLOWLIST,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        triage_llm_fn=_make_triage_fn("bucket_c"),
        search_fn=_make_search_fn(results=[]),
        reconciliation_llm_fn=_make_reconciliation_fn(
            groups=[], distinct=[], unresolved=[]
        ),
    )

    assert result["tag"].claim_id == _CLAIM_ID
    assert result["tag"].claim_text == _CLAIM


# ---------------------------------------------------------------------------
# Injectable fake plumbing tests
# ---------------------------------------------------------------------------


def test_all_fakes_are_called():
    """
    With all fakes injected on a successful run, each fake is called at least
    once. Confirms the wiring is complete and no step is silently bypassed.
    """
    call_counts = {
        "triage": 0,
        "search": 0,
        "url_llm": 0,
        "fetch": 0,
        "finding_llm": 0,
        "reconciliation": 0,
    }

    def triage_fn(claim_text):
        call_counts["triage"] += 1
        return {"classification": "bucket_c", "reasoning": "contested"}

    def search_fn(query):
        call_counts["search"] += 1
        return _FAKE_SEARCH_RESULTS

    def url_llm_fn(claim_text, search_results):
        call_counts["url_llm"] += 1
        return {"url": _FAKE_URL_1}

    def fetch_fn(url):
        call_counts["fetch"] += 1
        return _FAKE_FETCH_RESULT

    def finding_llm_fn(document, claim_text):
        call_counts["finding_llm"] += 1
        return {
            "value_found": True,
            "claimed_value": (
                "approximately 60% of the global pure-play foundry market"
            ),
            "is_literal_value": True,
            "definition_found": True,
            "definition_text": (
                "pure-play foundry market excludes in-house IDM fabrication capacity"
            ),
        }

    def reconciliation_fn(claim_text, findings, feedback):
        call_counts["reconciliation"] += 1
        urls = [f["source_url"] for f in findings]
        if len(urls) >= 2:
            return {
                "groups": [
                    {
                        "member_source_urls": urls[:2],
                        "shared_definition_label": "pure-play foundry",
                        "reasoning": "Same scope.",
                    }
                ],
                "distinct": [],
                "unresolved": [],
            }
        return {"groups": [], "distinct": [], "unresolved": []}

    run_bucket_c_pipeline(
        _CLAIM,
        _ALLOWLIST,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        triage_llm_fn=triage_fn,
        search_fn=search_fn,
        url_llm_fn=url_llm_fn,
        fetch_fn=fetch_fn,
        finding_llm_fn=finding_llm_fn,
        reconciliation_llm_fn=reconciliation_fn,
    )

    for name, count in call_counts.items():
        assert count >= 1, f"fake '{name}' was never called"


def test_gather_not_called_when_triage_routes_away():
    """
    When triage returns "bucket_a", the search_fn is never called — gather
    is entirely skipped.
    """
    search_called = {"n": 0}

    def counting_search_fn(query):
        search_called["n"] += 1
        return []

    run_bucket_c_pipeline(
        _CLAIM,
        _ALLOWLIST,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        triage_llm_fn=_make_triage_fn("bucket_a"),
        search_fn=counting_search_fn,
    )

    assert search_called["n"] == 0


# ---------------------------------------------------------------------------
# Live API test (opt-in)
# ---------------------------------------------------------------------------


@pytest.mark.live_api
@pytest.mark.skipif(
    not os.getenv("RUN_LIVE_API"),
    reason="live API test; set RUN_LIVE_API=1 to run deliberately (costs money)",
)
def test_live_bucket_c_pipeline_tsmc_foundry_market_share():
    """
    Exercises the full real Bucket C chain with no injected fakes: real triage,
    real source gathering, real reconciliation.

    Non-deterministic: search results, model output, and fetched content vary
    across runs. Both "disambiguated" and "definitional_ambiguity_unresolved"
    are valid, honest outcomes — do NOT assert a specific overall_status.
    Occasional variation does not indicate a code defect. Requires
    OPENAI_API_KEY and TAVILY_API_KEY in the environment.
    """
    result = run_bucket_c_pipeline(
        "TSMC has roughly 60% of the foundry market",
        ["tsmc.com"],
        company_name="TSMC",
        claim_id="tsmc-c-live-001",
    )

    assert result["outcome"] == "completed", (
        f"Expected outcome='completed' but got {result['outcome']!r}. "
        f"Full result: {result}"
    )
    assert (
        isinstance(result["triage_reasoning"], str) and result["triage_reasoning"]
    ), f"triage_reasoning must be a non-empty string. Full result: {result}"

    tag = result["tag"]
    assert isinstance(
        tag, ClaimTag
    ), f"tag must be a ClaimTag instance. Full result: {result}"
    assert tag.bucket == "C", f"tag.bucket must be 'C'. Full result: {result}"
    assert isinstance(tag.source_plurality_evidence, SourcePluralityEvidence), (
        f"tag.source_plurality_evidence must be a SourcePluralityEvidence. "
        f"Full result: {result}"
    )
    assert (
        tag.source_plurality_evidence.sources_checked >= 0
    ), f"sources_checked must be >= 0. Full result: {result}"
    assert tag.overall_status in (
        "disambiguated",
        "definitional_ambiguity_unresolved",
    ), (
        f"overall_status must be 'disambiguated' or "
        f"'definitional_ambiguity_unresolved', got {tag.overall_status!r}. "
        f"Full result: {result}"
    )
