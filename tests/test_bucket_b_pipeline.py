"""
Tests for bucket_b_pipeline.py.

All unit tests inject fakes for search_fn, url_llm_fn, fetch_fn, and
criterion_evidence_fn. No real API calls are made.

Test organisation:
  - all six criteria succeed → six CriterionEvidence records, correct status
  - one criterion's URL fails url_compare → five succeed, partial result
  - fetch cache works: two criteria share the same URL → fetch_fn called once
  - cache does NOT persist across separate calls to run_bucket_b_pipeline
  - evidence_source_type set correctly via domain check (official vs third_party)
  - live API smoke test (opt-in via RUN_LIVE_API=1)
"""

import os

import pytest

from bucket_b_pipeline import run_bucket_b_pipeline
from criterion_evidence import NZIF_CRITERIA
from quote_match import MINIMUM_QUOTE_LENGTH_CHARS

TSMC_ALLOWLIST = ["tsmc.com", "pr.tsmc.com"]

# ---------------------------------------------------------------------------
# Shared fake helpers
# ---------------------------------------------------------------------------

FAKE_URL = "https://sustainability.tsmc.com/report"
FAKE_DOCUMENT = (
    "TSMC has set a long term net zero goal consistent with global climate "
    "targets. We govern emissions reductions at board level and disclose "
    "scope 1, 2 and material scope 3 emissions. Short and medium term targets "
    "for scope 1, 2 and material scope 3 emissions are aligned with "
    "science-based net zero pathways. We have developed a quantified "
    "decarbonisation plan covering scope 1, 2, and material scope 3. "
    "Current emissions performance is tracked against our net zero pathway."
)


def _make_search_fn(url: str = FAKE_URL):
    """Returns a search_fn that always returns one result for any query."""

    def search_fn(query):
        return [{"url": url, "title": "TSMC Sustainability", "snippet": query}]

    return search_fn


def _make_url_llm_fn(url: str = FAKE_URL):
    """Returns a url_llm_fn that always selects the given URL."""

    def url_llm_fn(company_name, criterion_name, criterion_text, search_results):
        return {"url": url}

    return url_llm_fn


def _make_fetch_fn(text: str = FAKE_DOCUMENT):
    """Returns a fetch_fn that always succeeds with the given text."""

    def fetch_fn(url):
        return {
            "success": True,
            "text": text,
            "content_type": "text/html",
            "failure_reason": None,
        }

    return fetch_fn


def _make_criterion_evidence_fn(
    excerpt: str = "TSMC has set a long term net zero goal.",
):
    """Returns a criterion_evidence_fn that always succeeds."""

    def criterion_evidence_fn(document, criterion_name, criterion_text):
        return {
            "status": "excerpt_verified",
            "excerpt": excerpt,
            "top_score": 95.0,
            "attempts": 1,
            "last_attempt_status": "excerpt_verified",
        }

    return criterion_evidence_fn


# ---------------------------------------------------------------------------
# All six criteria succeed
# ---------------------------------------------------------------------------


def test_all_six_criteria_succeed_produces_six_evidence_records():
    """
    When every step succeeds for all six criteria, the resulting ClaimTag has
    six CriterionEvidence records and overall_status "criteria_evidence_gathered".
    """
    tag = run_bucket_b_pipeline(
        company_name="TSMC",
        claim_id="tsmc-b-001",
        allowlist=TSMC_ALLOWLIST,
        search_fn=_make_search_fn(),
        url_llm_fn=_make_url_llm_fn(),
        fetch_fn=_make_fetch_fn(),
        criterion_evidence_fn=_make_criterion_evidence_fn(),
    )

    assert tag.bucket == "B"
    assert tag.claim_id == "tsmc-b-001"
    assert tag.criteria_evidence is not None
    assert len(tag.criteria_evidence) == 6
    assert {ce.criterion_name for ce in tag.criteria_evidence} == set(
        NZIF_CRITERIA.keys()
    )
    assert tag.overall_status == "criteria_evidence_gathered"


def test_all_six_criteria_evidence_records_have_correct_fields():
    """
    Each CriterionEvidence record must carry the correct criterion_text (from
    NZIF_CRITERIA), the excerpt from find_criterion_evidence, the URL, and the
    source type.
    """
    tag = run_bucket_b_pipeline(
        company_name="TSMC",
        claim_id="tsmc-b-001",
        allowlist=TSMC_ALLOWLIST,
        search_fn=_make_search_fn(),
        url_llm_fn=_make_url_llm_fn(),
        fetch_fn=_make_fetch_fn(),
        criterion_evidence_fn=_make_criterion_evidence_fn("a verbatim excerpt"),
    )

    for ce in tag.criteria_evidence:
        assert ce.criterion_text == NZIF_CRITERIA[ce.criterion_name]
        assert ce.evidence_text == "a verbatim excerpt"
        assert ce.evidence_source_url == FAKE_URL
        assert (
            ce.evidence_source_type == "official"
        )  # sustainability.tsmc.com matches tsmc.com


# ---------------------------------------------------------------------------
# One criterion's URL fails url_compare
# ---------------------------------------------------------------------------


def test_one_criterion_url_not_from_search_results_skips_that_criterion():
    """
    When the LLM proposes a URL that did not come from the search results for
    one specific criterion, that criterion is skipped. The other five still
    succeed. The result is partial, not a total failure.
    """
    GOOD_URL = "https://sustainability.tsmc.com/report"
    BAD_URL = "https://hallucinated.example.com/not-in-results"

    call_count = {"n": 0}

    def url_llm_fn(company_name, criterion_name, criterion_text, search_results):
        call_count["n"] += 1
        # Return a bad URL only for the third criterion encountered
        if call_count["n"] == 3:
            return {"url": BAD_URL}
        return {"url": GOOD_URL}

    tag = run_bucket_b_pipeline(
        company_name="TSMC",
        claim_id="tsmc-b-002",
        allowlist=TSMC_ALLOWLIST,
        search_fn=_make_search_fn(GOOD_URL),
        url_llm_fn=url_llm_fn,
        fetch_fn=_make_fetch_fn(),
        criterion_evidence_fn=_make_criterion_evidence_fn(),
    )

    assert tag.criteria_evidence is not None
    assert len(tag.criteria_evidence) == 5
    assert tag.overall_status == "criteria_evidence_gathered"


# ---------------------------------------------------------------------------
# Fetch cache: same URL shared by multiple criteria
# ---------------------------------------------------------------------------


def test_fetch_cache_prevents_duplicate_fetches_for_shared_url():
    """
    When two different criteria resolve to the same source URL, the fetch_fn
    must be called exactly once — not once per criterion. The cache hit on the
    second criterion must use the stored text, not trigger a new fetch.

    This tests the mechanism (call count), not just the outcome, following the
    same pattern as extraction.py's injection tests.
    """
    fetch_call_count = {"n": 0}

    def counting_fetch_fn(url):
        fetch_call_count["n"] += 1
        return {
            "success": True,
            "text": FAKE_DOCUMENT,
            "content_type": "text/html",
            "failure_reason": None,
        }

    tag = run_bucket_b_pipeline(
        company_name="TSMC",
        claim_id="tsmc-b-003",
        allowlist=TSMC_ALLOWLIST,
        search_fn=_make_search_fn(FAKE_URL),  # every criterion → same URL
        url_llm_fn=_make_url_llm_fn(FAKE_URL),
        fetch_fn=counting_fetch_fn,
        criterion_evidence_fn=_make_criterion_evidence_fn(),
    )

    assert tag.criteria_evidence is not None
    assert len(tag.criteria_evidence) == 6
    # Six criteria, one shared URL → fetch must be called exactly once
    assert fetch_call_count["n"] == 1


# ---------------------------------------------------------------------------
# Cache does NOT persist across separate calls
# ---------------------------------------------------------------------------


def test_fetch_cache_does_not_persist_across_separate_pipeline_calls():
    """
    The fetch cache is scoped to one run_bucket_b_pipeline() call. Calling it
    twice — even for the same company and URL — must fetch on each call. The
    cache must NOT be global state surviving between calls.
    """
    fetch_call_count = {"n": 0}

    def counting_fetch_fn(url):
        fetch_call_count["n"] += 1
        return {
            "success": True,
            "text": FAKE_DOCUMENT,
            "content_type": "text/html",
            "failure_reason": None,
        }

    common_kwargs = dict(
        company_name="TSMC",
        claim_id="tsmc-b-004",
        allowlist=TSMC_ALLOWLIST,
        search_fn=_make_search_fn(FAKE_URL),
        url_llm_fn=_make_url_llm_fn(FAKE_URL),
        fetch_fn=counting_fetch_fn,
        criterion_evidence_fn=_make_criterion_evidence_fn(),
    )

    run_bucket_b_pipeline(**common_kwargs)
    first_call_count = fetch_call_count["n"]

    run_bucket_b_pipeline(**common_kwargs)
    second_call_count = fetch_call_count["n"]

    # Both calls hit the same URL for all six criteria. If the cache were
    # global, the second call would contribute 0 fetches. It must contribute
    # exactly the same count as the first call.
    assert first_call_count > 0
    assert (second_call_count - first_call_count) == first_call_count


# ---------------------------------------------------------------------------
# evidence_source_type: official vs third_party
# ---------------------------------------------------------------------------


def test_evidence_source_type_is_official_when_url_matches_allowlist():
    """
    A URL whose domain is in the allowlist produces evidence_source_type="official".
    """
    official_url = "https://pr.tsmc.com/english/news/sustainability"

    tag = run_bucket_b_pipeline(
        company_name="TSMC",
        claim_id="tsmc-b-005",
        allowlist=TSMC_ALLOWLIST,
        criteria=["ambition"],
        search_fn=_make_search_fn(official_url),
        url_llm_fn=_make_url_llm_fn(official_url),
        fetch_fn=_make_fetch_fn(),
        criterion_evidence_fn=_make_criterion_evidence_fn(),
    )

    assert tag.criteria_evidence is not None
    assert len(tag.criteria_evidence) == 1
    assert tag.criteria_evidence[0].evidence_source_type == "official"


def test_evidence_source_type_is_third_party_when_url_not_in_allowlist():
    """
    A URL whose domain is NOT in the allowlist produces
    evidence_source_type="third_party", regardless of whether the content
    looks authoritative.
    """
    third_party_url = "https://www.bloomberg.com/tsmc-sustainability-article"

    tag = run_bucket_b_pipeline(
        company_name="TSMC",
        claim_id="tsmc-b-006",
        allowlist=TSMC_ALLOWLIST,
        criteria=["ambition"],
        search_fn=_make_search_fn(third_party_url),
        url_llm_fn=_make_url_llm_fn(third_party_url),
        fetch_fn=_make_fetch_fn(),
        criterion_evidence_fn=_make_criterion_evidence_fn(),
    )

    assert tag.criteria_evidence is not None
    assert len(tag.criteria_evidence) == 1
    assert tag.criteria_evidence[0].evidence_source_type == "third_party"


# ---------------------------------------------------------------------------
# No criteria succeed → incomplete
# ---------------------------------------------------------------------------


def test_no_criteria_succeed_produces_incomplete_status():
    """
    If no criterion produces verified evidence, overall_status is "incomplete"
    (criteria_evidence=None, computed by tag_schema).
    """

    def failing_search_fn(query):
        return []  # no results for any criterion

    tag = run_bucket_b_pipeline(
        company_name="TSMC",
        claim_id="tsmc-b-007",
        allowlist=TSMC_ALLOWLIST,
        search_fn=failing_search_fn,
    )

    assert tag.criteria_evidence is None
    assert tag.overall_status == "incomplete"


# ---------------------------------------------------------------------------
# Search query construction regression
# ---------------------------------------------------------------------------


def test_search_query_uses_first_clause_of_criterion_text_not_criterion_name():
    """
    Regression: the original query used company_name + criterion_name (e.g.
    "TSMC ambition"), which returned zero Tavily results for every criterion in
    the first live run. The fix is company_name + first clause of criterion_text
    (text before the first "."), confirmed to return 5 results for all six real
    NZIF criteria in direct Tavily live tests.

    This test asserts the EXACT query string passed to search_fn for each
    criterion, so a future edit that reverts to the weak construction fails
    here rather than silently reintroducing a bug only catchable by a live run.
    """
    recorded_queries: list[str] = []

    def recording_search_fn(query):
        recorded_queries.append(query)
        return [{"url": FAKE_URL, "title": "T", "snippet": query}]

    run_bucket_b_pipeline(
        company_name="TSMC",
        claim_id="tsmc-b-query-regression",
        allowlist=TSMC_ALLOWLIST,
        search_fn=recording_search_fn,
        url_llm_fn=_make_url_llm_fn(),
        fetch_fn=_make_fetch_fn(),
        criterion_evidence_fn=_make_criterion_evidence_fn(),
    )

    assert len(recorded_queries) == len(NZIF_CRITERIA)
    for criterion_name, criterion_text in NZIF_CRITERIA.items():
        expected_first_clause = criterion_text.split(".")[0]
        expected_query = f"TSMC {expected_first_clause}"
        assert expected_query in recorded_queries, (
            f"Query for '{criterion_name}' was not '{expected_query}'. "
            f"Actual queries: {recorded_queries}"
        )


# ---------------------------------------------------------------------------
# Live API test (opt-in)
# ---------------------------------------------------------------------------


@pytest.mark.live_api
@pytest.mark.skipif(
    not os.getenv("RUN_LIVE_API"),
    reason="live API test; set RUN_LIVE_API=1 to run deliberately (costs money)",
)
def test_live_tsmc_bucket_b_ambition():
    """
    Exercises the full real Bucket B chain for TSMC's "ambition" criterion:
    real Tavily Search, real OpenAI call for URL selection, real HTTP fetch,
    and real criterion_evidence extraction.

    This is the first live run of Bucket B's full chain. It may surface real
    issues no mock can — the same lesson already learned from page_fetch.py's
    chunked-encoding bug, which only appeared against real pages, not fixtures.
    Non-deterministic: search results, model output, and fetched content vary
    across runs. Occasional failure does not indicate a code defect. Requires
    OPENAI_API_KEY and TAVILY_API_KEY in the environment.

    When evidence IS found (overall_status == "criteria_evidence_gathered"),
    the assertions below verify it is real, well-formed evidence, not just that
    the pipeline didn't crash. A PASSED result should be self-explanatory
    without a separate manual script.
    """
    tag = run_bucket_b_pipeline(
        company_name="TSMC",
        claim_id="tsmc-b-live-001",
        allowlist=TSMC_ALLOWLIST,
        criteria=["ambition"],
    )

    assert tag.bucket == "B"
    assert tag.overall_status in ("criteria_evidence_gathered", "incomplete")

    if tag.overall_status == "incomplete":
        # Acceptable live outcome: search returned nothing usable or the model
        # couldn't find a verifiable excerpt on this run. Not a code defect.
        print(
            "\nincomplete: ambition evidence was not found on this run — "
            "this can happen on a single live attempt and is not necessarily "
            "a regression"
        )
        return

    # Evidence was found: assert it is real and well-formed, so PASSED is
    # actually meaningful rather than requiring manual follow-up inspection.
    assert tag.criteria_evidence is not None
    assert len(tag.criteria_evidence) == 1

    ce = tag.criteria_evidence[0]

    # criterion_text must be the verified primary-source wording, not some
    # other string that snuck in via a code path that doesn't use NZIF_CRITERIA.
    assert ce.criterion_text == NZIF_CRITERIA["ambition"]

    # evidence_text must be a real excerpt, not an empty string or a trivially
    # short response. Threshold reuses MINIMUM_QUOTE_LENGTH_CHARS from
    # quote_match.py (15 chars), the same minimum already enforced on Bucket A
    # quote evidence — a reasonable floor for any verified text excerpt.
    assert len(ce.evidence_text) > MINIMUM_QUOTE_LENGTH_CHARS

    # The source must be TSMC's own domain. "tsmc.com" appearing anywhere in
    # the URL is a sanity check that the pipeline didn't select a wrong-company
    # or hallucinated source.
    assert "tsmc.com" in ce.evidence_source_url

    # evidence_source_type must be one of the two defined values from
    # CriterionEvidence's contract. Any other value means check_domain's result
    # is being read incorrectly.
    assert ce.evidence_source_type in ("official", "third_party")
