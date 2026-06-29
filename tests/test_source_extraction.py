"""
Tests for source_extraction.py: find_source_finding and gather_source_findings.

All unit tests inject fake llm_fn / search_fn / fetch_fn — no real API calls.
quote_match runs for real against controlled document text (deterministic).
One live test (opt-in via RUN_LIVE_API=1) calls gather_source_findings on
the real TSMC market-share claim.
"""

import os

import pytest

from source_extraction import find_source_finding, gather_source_findings
from tag_schema import SourceFinding

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# A short document that contains both a literal value and a scope definition,
# verbatim, so quote_match can find them.
_DOC_WITH_BOTH = (
    "According to TrendForce, TSMC captured approximately 60% of the global "
    "pure-play foundry market in 2023, where the pure-play foundry market is "
    "defined as semiconductor manufacturers that exclusively fabricate chips "
    "for external customers without designing their own products."
)

_VALUE_TEXT = (
    "TSMC captured approximately 60% of the global pure-play foundry market in 2023"
)
_DEFINITION_TEXT = (
    "the pure-play foundry market is defined as semiconductor manufacturers "
    "that exclusively fabricate chips for external customers without designing "
    "their own products"
)


def _both_found_fn(document, claim_text):
    return {
        "value_found": True,
        "claimed_value": _VALUE_TEXT,
        "is_literal_value": True,
        "definition_found": True,
        "definition_text": _DEFINITION_TEXT,
    }


def _value_only_fn(document, claim_text):
    return {
        "value_found": True,
        "claimed_value": _VALUE_TEXT,
        "is_literal_value": True,
        "definition_found": False,
        "definition_text": None,
    }


def _definition_only_fn(document, claim_text):
    return {
        "value_found": False,
        "claimed_value": None,
        "is_literal_value": False,
        "definition_found": True,
        "definition_text": _DEFINITION_TEXT,
    }


def _nothing_found_fn(document, claim_text):
    return {
        "value_found": False,
        "claimed_value": None,
        "is_literal_value": False,
        "definition_found": False,
        "definition_text": None,
    }


def _bad_value_fn(document, claim_text):
    """value_found=True but the text isn't actually in the document."""
    return {
        "value_found": True,
        "claimed_value": "This sentence does not appear anywhere in the document at all",
        "is_literal_value": True,
        "definition_found": True,
        "definition_text": _DEFINITION_TEXT,
    }


def _bad_definition_fn(document, claim_text):
    """definition_found=True but the text isn't actually in the document."""
    return {
        "value_found": True,
        "claimed_value": _VALUE_TEXT,
        "is_literal_value": True,
        "definition_found": True,
        "definition_text": "This definition does not appear anywhere in the document at all",
    }


# ---------------------------------------------------------------------------
# find_source_finding: success cases
# ---------------------------------------------------------------------------


def test_both_value_and_definition_verified():
    """Both fields verified — SourceFinding returned with both statuses unique."""
    result = find_source_finding(
        document=_DOC_WITH_BOTH,
        claim_text="TSMC has roughly 60% of the foundry market",
        source_url="https://example.com/report",
        source_type="third_party",
        llm_fn=_both_found_fn,
    )
    assert isinstance(result, SourceFinding)
    assert result.value_found is True
    assert result.value_verification_status == "unique"
    assert result.definition_found is True
    assert result.definition_verification_status == "unique"
    assert result.source_url == "https://example.com/report"
    assert result.source_type == "third_party"
    assert result.is_literal_value is True


def test_value_verified_no_definition():
    """value passes quote_match; no definition proposed — still a valid finding."""
    result = find_source_finding(
        document=_DOC_WITH_BOTH,
        claim_text="TSMC has roughly 60% of the foundry market",
        source_url="https://example.com/report",
        source_type="third_party",
        llm_fn=_value_only_fn,
    )
    assert isinstance(result, SourceFinding)
    assert result.value_verification_status == "unique"
    assert result.definition_found is False
    assert result.definition_verification_status is None
    assert result.definition_text is None


def test_definition_verified_no_value():
    """
    definition passes quote_match; value_found=False — still a valid finding.
    is_literal_value must be False (not None) when value_found=False.
    """
    result = find_source_finding(
        document=_DOC_WITH_BOTH,
        claim_text="TSMC has roughly 60% of the foundry market",
        source_url="https://example.com/report",
        source_type="third_party",
        llm_fn=_definition_only_fn,
    )
    assert isinstance(result, SourceFinding)
    assert result.value_found is False
    assert result.claimed_value is None
    assert result.is_literal_value is False  # False, not None
    assert result.value_verification_status is None
    assert result.definition_verification_status == "unique"


# ---------------------------------------------------------------------------
# find_source_finding: partial failure cases
# ---------------------------------------------------------------------------


def test_value_verification_fails_but_definition_succeeds_returns_finding():
    """
    value_found=True but quote_match rejects it (no_match).
    definition_found=True and quote_match succeeds.
    Floor rule: definition is verified, so SourceFinding IS returned.
    The value's real failure status is recorded honestly.
    """
    result = find_source_finding(
        document=_DOC_WITH_BOTH,
        claim_text="TSMC has roughly 60% of the foundry market",
        source_url="https://example.com/report",
        source_type="third_party",
        llm_fn=_bad_value_fn,
    )
    assert isinstance(result, SourceFinding)
    assert result.value_verification_status != "unique"
    assert result.definition_verification_status == "unique"


def test_definition_verification_fails_but_value_succeeds_returns_finding():
    """
    Mirror of the above: definition_found=True but quote_match rejects it.
    value_found=True and quote_match succeeds.
    Floor rule: value is verified, so SourceFinding IS returned.
    The definition's real failure status is recorded honestly.

    This covers the opposite code path from the bad-value/good-definition test:
    value_verification_status and definition_verification_status are computed
    by two independent match_quote calls, so a bug in one path would not
    necessarily be caught by a test of the other direction.
    """
    result = find_source_finding(
        document=_DOC_WITH_BOTH,
        claim_text="TSMC has roughly 60% of the foundry market",
        source_url="https://example.com/report",
        source_type="third_party",
        llm_fn=_bad_definition_fn,
    )
    assert isinstance(result, SourceFinding)
    assert result.value_verification_status == "unique"
    assert result.definition_verification_status != "unique"


# ---------------------------------------------------------------------------
# find_source_finding: floor rule — None returned
# ---------------------------------------------------------------------------


def test_both_not_found_returns_none():
    """value_found=False and definition_found=False — floor rule, return None."""
    result = find_source_finding(
        document=_DOC_WITH_BOTH,
        claim_text="TSMC has roughly 60% of the foundry market",
        source_url="https://example.com/report",
        source_type="third_party",
        llm_fn=_nothing_found_fn,
    )
    assert result is None


def test_both_verification_fail_returns_none():
    """Both fields proposed but neither passes quote_match — return None."""

    def bad_both_fn(document, claim_text):
        return {
            "value_found": True,
            "claimed_value": "This sentence is nowhere in the document at all",
            "is_literal_value": True,
            "definition_found": True,
            "definition_text": "This definition is also nowhere in the document",
        }

    result = find_source_finding(
        document=_DOC_WITH_BOTH,
        claim_text="TSMC has roughly 60% of the foundry market",
        source_url="https://example.com/report",
        source_type="third_party",
        llm_fn=bad_both_fn,
    )
    assert result is None


# ---------------------------------------------------------------------------
# find_source_finding: malformed LLM response
# ---------------------------------------------------------------------------


def test_malformed_json_returns_none():
    """llm_fn that raises (simulating bad JSON) returns None, no exception."""

    def raising_fn(document, claim_text):
        raise ValueError("simulated bad JSON")

    result = find_source_finding(
        document=_DOC_WITH_BOTH,
        claim_text="TSMC has roughly 60% of the foundry market",
        source_url="https://example.com/report",
        source_type="third_party",
        llm_fn=raising_fn,
    )
    assert result is None


def test_missing_required_field_returns_none():
    """Response missing a required field returns None, no exception."""

    def incomplete_fn(document, claim_text):
        return {
            "value_found": True,
            "claimed_value": _VALUE_TEXT,
            # missing is_literal_value, definition_found, definition_text
        }

    result = find_source_finding(
        document=_DOC_WITH_BOTH,
        claim_text="TSMC has roughly 60% of the foundry market",
        source_url="https://example.com/report",
        source_type="third_party",
        llm_fn=incomplete_fn,
    )
    assert result is None


def test_wrong_type_for_value_found_returns_none():
    """value_found that is not a bool returns None."""

    def bad_type_fn(document, claim_text):
        return {
            "value_found": "yes",  # string, not bool
            "claimed_value": _VALUE_TEXT,
            "is_literal_value": True,
            "definition_found": False,
            "definition_text": None,
        }

    result = find_source_finding(
        document=_DOC_WITH_BOTH,
        claim_text="TSMC has roughly 60% of the foundry market",
        source_url="https://example.com/report",
        source_type="third_party",
        llm_fn=bad_type_fn,
    )
    assert result is None


# ---------------------------------------------------------------------------
# find_source_finding: is_literal_value correctness
# ---------------------------------------------------------------------------


def test_is_literal_value_is_false_not_none_when_value_not_found():
    """
    is_literal_value must be False (not None) when value_found=False,
    regardless of what the model says. Confirmed via definition_only path.
    """
    result = find_source_finding(
        document=_DOC_WITH_BOTH,
        claim_text="TSMC has roughly 60% of the foundry market",
        source_url="https://example.com/report",
        source_type="third_party",
        llm_fn=_definition_only_fn,
    )
    assert result is not None
    assert result.is_literal_value is False
    assert result.is_literal_value is not None


# ---------------------------------------------------------------------------
# gather_source_findings: orchestration
# ---------------------------------------------------------------------------

_FAKE_SEARCH_RESULTS = [
    {"url": "https://example.com/report", "title": "Foundry Report", "snippet": "..."}
]
_FAKE_FETCH_RESULT = {
    "success": True,
    "text": _DOC_WITH_BOTH,
    "content_type": "text/html",
    "failure_reason": None,
}


def _make_search_fn(results=_FAKE_SEARCH_RESULTS):
    def fn(query):
        return results

    return fn


def _make_url_llm_fn(url="https://example.com/report"):
    def fn(claim_text, search_results):
        return {"url": url}

    return fn


def _make_fetch_fn(result=_FAKE_FETCH_RESULT):
    def fn(url):
        return result

    return fn


def test_gather_returns_target_count_when_every_iteration_succeeds():
    """
    With target_source_count=3 and every iteration producing a valid finding,
    exactly 3 findings are returned.
    """
    results = gather_source_findings(
        claim_text="TSMC has roughly 60% of the foundry market",
        allowlist=["tsmc.com"],
        target_source_count=3,
        search_fn=_make_search_fn(),
        url_llm_fn=_make_url_llm_fn(),
        fetch_fn=_make_fetch_fn(),
        finding_llm_fn=_both_found_fn,
    )
    assert len(results) == 3
    assert all(isinstance(f, SourceFinding) for f in results)


def test_failed_iterations_do_not_count_toward_target():
    """
    Some iterations fail (e.g. no search results). Only iterations that
    produce a real SourceFinding count toward the target.

    Setup: first 2 calls return empty results (failures), then 2 return real
    results. With target=2 and hard cap = 2*3=6, we should get exactly 2 findings.
    """
    call_count = {"n": 0}

    def alternating_search(query):
        call_count["n"] += 1
        if call_count["n"] <= 2:
            return []
        return _FAKE_SEARCH_RESULTS

    results = gather_source_findings(
        claim_text="TSMC has roughly 60% of the foundry market",
        allowlist=["tsmc.com"],
        target_source_count=2,
        search_fn=alternating_search,
        url_llm_fn=_make_url_llm_fn(),
        fetch_fn=_make_fetch_fn(),
        finding_llm_fn=_both_found_fn,
    )
    assert len(results) == 2


def test_hard_cap_stops_loop_when_every_iteration_fails():
    """
    Every iteration fails (no search results). Hard cap = target * 3 stops
    the loop and returns an empty list, no infinite loop.
    """
    call_count = {"n": 0}

    def always_empty(query):
        call_count["n"] += 1
        return []

    results = gather_source_findings(
        claim_text="TSMC has roughly 60% of the foundry market",
        allowlist=["tsmc.com"],
        target_source_count=3,
        search_fn=always_empty,
        url_llm_fn=_make_url_llm_fn(),
        fetch_fn=_make_fetch_fn(),
        finding_llm_fn=_both_found_fn,
    )
    assert results == []
    assert call_count["n"] == 3 * 3  # hard cap = target * _ATTEMPTS_MULTIPLIER


def test_in_call_url_cache_deduplicates_fetches():
    """
    If two iterations' searches return the same URL, fetch_fn is called only
    once — the second iteration uses the cached document.
    """
    fetch_call_count = {"n": 0}

    def counting_fetch(url):
        fetch_call_count["n"] += 1
        return _FAKE_FETCH_RESULT

    results = gather_source_findings(
        claim_text="TSMC has roughly 60% of the foundry market",
        allowlist=["tsmc.com"],
        target_source_count=2,
        search_fn=_make_search_fn(),  # always returns same URL
        url_llm_fn=_make_url_llm_fn(),
        fetch_fn=counting_fetch,
        finding_llm_fn=_both_found_fn,
    )
    assert len(results) == 2
    assert fetch_call_count["n"] == 1, (
        "fetch_fn should be called once even across multiple iterations with "
        "the same URL; second iteration must use the in-call cache"
    )


def test_gather_returns_empty_list_when_finding_always_none():
    """
    Every find_source_finding returns None (floor rule). Hard cap is hit.
    gather_source_findings returns an empty list, no crash.
    """
    results = gather_source_findings(
        claim_text="TSMC has roughly 60% of the foundry market",
        allowlist=["tsmc.com"],
        target_source_count=2,
        search_fn=_make_search_fn(),
        url_llm_fn=_make_url_llm_fn(),
        fetch_fn=_make_fetch_fn(),
        finding_llm_fn=_nothing_found_fn,
    )
    assert results == []


# ---------------------------------------------------------------------------
# Live test — opt-in only, costs money
# ---------------------------------------------------------------------------

# TSMC's own investor-relations and press-release domains, used only to
# determine source_type. For this Bucket C claim (market share), we expect
# all sources to be "third_party" (analyst reports, not TSMC's own filings)
# — that is the correct, expected outcome for a definitionally contested
# claim, not a failure. See module docstring's ALLOWLIST NOTE.
_TSMC_ALLOWLIST = [
    "tsmc.com",
    "investor.tsmc.com",
    "ir.tsmc.com",
]


@pytest.mark.live_api
@pytest.mark.skipif(
    not os.getenv("RUN_LIVE_API"),
    reason="live API test; set RUN_LIVE_API=1 to run deliberately (costs money)",
)
def test_live_gather_tsmc_foundry_market_share():
    """
    Calls gather_source_findings on the real Bucket C design claim with no
    injected fakes (real Tavily search, real OpenAI calls, real HTTP fetches).
    Requires OPENAI_API_KEY and TAVILY_API_KEY in the environment.

    ALLOWLIST NOTE: for this Bucket C claim, most or all findings will be
    "third_party" — that is correct and expected. The allowlist is used only
    to determine source_type, not to gate or reject sources.

    Assertions are tolerant of real-world variability (search results change,
    pages go down), but assert that whatever is returned has the correct shape.
    """
    results = gather_source_findings(
        claim_text="TSMC has roughly 60% of the foundry market",
        allowlist=_TSMC_ALLOWLIST,
        target_source_count=3,
    )

    # Not asserting a specific count — real pages go down, search results vary.
    # Assert the shape of whatever was found is correct.
    for finding in results:
        assert isinstance(finding, SourceFinding)
        assert finding.source_url.startswith("http")
        assert finding.source_type in ("official", "third_party")
        assert isinstance(finding.value_found, bool)
        assert isinstance(finding.definition_found, bool)
        assert isinstance(finding.is_literal_value, bool)
        # Floor rule: at least one field must be verified
        assert (
            finding.value_verification_status == "unique"
            or finding.definition_verification_status == "unique"
        ), (
            f"Floor rule violated for {finding.source_url}: "
            f"value_verification_status={finding.value_verification_status}, "
            f"definition_verification_status={finding.definition_verification_status}"
        )
        # is_literal_value must be False (not None) when value_found=False
        if not finding.value_found:
            assert finding.is_literal_value is False

    if results:
        # If we got at least one result, log the first finding's details so
        # a live run's output is self-documenting (same principle as
        # bucket_b_pipeline.py's enriched live test assertions).
        first = results[0]
        assert first.source_type in ("official", "third_party")
