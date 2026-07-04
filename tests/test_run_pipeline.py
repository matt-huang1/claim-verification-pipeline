"""
Tests for run_pipeline.py.

All unit tests inject fakes for every LLM and network call — no real API calls.

Test organisation:
  - Routing tests: triage-driven routing to each bucket
  - Explicit bucket override tests: bucket= supplied, triage skipped
  - Return shape tests: always four fields, tag types
  - Outcome string tests: each bucket's success/failure outcome strings
  - Live API smoke tests (opt-in via RUN_LIVE_API=1)
"""

import os

import pytest

from agent_eval.run_pipeline import run_pipeline
from agent_eval.tag_schema import ClaimTag
from agent_eval.web_search import SearchUnavailable

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_CLAIM_A = "TSMC's revenue was $69.3 billion in FY2023."
_CLAIM_C = "TSMC has roughly 60% of the foundry market"
_CLAIM_D = (
    "Without TSMC, the global climate transition would be set "
    "back by a decade because advanced chips are essential for "
    "clean energy technology."
)
_ALLOWLIST = ["tsmc.com"]
_COMPANY = "TSMC"
_CLAIM_ID = "tsmc-dispatcher-001"

_FAKE_URL = "https://tsmc.com/annual-report"
_FAKE_URL_2 = "https://icinsights.com/tsmc-market-share"
_FAKE_DOCUMENT_A = (
    "TSMC reported net revenue of NT$2.16 trillion (approximately "
    "US$69.3 billion) for fiscal year 2023, a decrease of 4.5% from 2022."
)
_FAKE_DOCUMENT_C = (
    "TSMC held approximately 60% of the global pure-play foundry market "
    "by revenue in 2023. The pure-play foundry market excludes in-house "
    "IDM fabrication capacity."
)

# ---------------------------------------------------------------------------
# Shared fake helpers
# ---------------------------------------------------------------------------


def _triage_fn(classification, reasoning="some triage reasoning"):
    def fn(claim_text):
        return {"classification": classification, "reasoning": reasoning}

    return fn


def _search_fn(results):
    def fn(query):
        return results

    return fn


def _fetch_fn_success(document):
    def fn(url):
        return {
            "success": True,
            "text": document,
            "content_type": "text/html",
            "failure_reason": None,
        }

    return fn


def _fetch_fn_fail():
    def fn(url):
        return {
            "success": False,
            "text": None,
            "content_type": None,
            "failure_reason": "not_found",
        }

    return fn


def _extraction_llm_fn(url=_FAKE_URL, quote=None):
    _quote = quote or "US$69.3 billion"

    def fn(claim_text, feedback, search_results):
        return {"url": url, "quote": _quote}

    return fn


def _b_url_llm_fn(url=_FAKE_URL):
    def fn(company_name, criterion_name, criterion_text, search_results):
        return {"url": url}

    return fn


def _b_criterion_evidence_fn_success():
    def fn(document, criterion_name, criterion_text, **kwargs):
        return {"status": "excerpt_verified", "excerpt": "TSMC has a net-zero target."}

    return fn


def _b_criterion_evidence_fn_fail():
    def fn(document, criterion_name, criterion_text, **kwargs):
        return {"status": "not_found_after_retries"}

    return fn


def _c_url_llm_fn(url=_FAKE_URL_2):
    def fn(claim_text, search_results):
        return {"url": url}

    return fn


def _c_finding_llm_fn():
    def fn(document, claim_text):
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

    return fn


def _c_reconciliation_fn_group():
    def fn(claim_text, findings, feedback):
        urls = [f["source_url"] for f in findings if f.get("source_url")]
        if len(urls) >= 2:
            return {
                "groups": [
                    {
                        "member_source_urls": urls[:2],
                        "shared_definition_label": "pure-play foundry market",
                        "reasoning": "Both exclude IDM in-house capacity.",
                    }
                ],
                "distinct": [],
                "unresolved": [],
            }
        return {"groups": [], "distinct": [], "unresolved": []}

    return fn


def _c_reconciliation_fn_no_group():
    def fn(claim_text, findings, feedback):
        urls = [f["source_url"] for f in findings if f.get("source_url")]
        return {
            "groups": [],
            "distinct": [],
            "unresolved": [
                {"source_url": u, "reasoning": "Scope unclear."} for u in urls
            ],
        }

    return fn


def _d_llm_fn_explicit():
    """Returns assumptions/causal_steps both with present_in_claim=True."""

    def fn(claim_text, feedback):
        return {
            "assumptions": [
                {
                    "text": "advanced chips are essential for clean energy",
                    "present_in_claim": True,
                }
            ],
            "causal_steps": [
                {
                    "text": "no TSMC → no advanced chips → slower climate tech",
                    "present_in_claim": True,
                }
            ],
        }

    return fn


def _d_llm_fn_not_stated():
    """Returns assumptions/causal_steps both with present_in_claim=False."""

    def fn(claim_text, feedback):
        return {
            "assumptions": [
                {"text": "some unstated assumption", "present_in_claim": False}
            ],
            "causal_steps": [{"text": "some unstated step", "present_in_claim": False}],
        }

    return fn


# ---------------------------------------------------------------------------
# Bucket C fake search results — two distinct URLs so reconcile can group
# ---------------------------------------------------------------------------

_FAKE_C_SEARCH = [
    {"url": _FAKE_URL_2, "title": "IC Insights TSMC", "snippet": "foundry market 60%"},
    {
        "url": "https://trendforce.com/foundry",
        "title": "TrendForce",
        "snippet": "foundry share",
    },
]

# ---------------------------------------------------------------------------
# Routing tests (triage-driven)
# ---------------------------------------------------------------------------


def test_triage_bucket_a_routes_to_bucket_a():
    result = run_pipeline(
        _CLAIM_A,
        _ALLOWLIST,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        triage_llm_fn=_triage_fn("bucket_a", "single authoritative source"),
        extraction_search_fn=_search_fn([]),  # no results → unverifiable quickly
    )
    assert result["bucket"] == "A"
    assert result["triage_reasoning"] == "single authoritative source"


def test_triage_bucket_c_routes_to_bucket_c():
    result = run_pipeline(
        _CLAIM_C,
        _ALLOWLIST,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        triage_llm_fn=_triage_fn("bucket_c"),
        bucket_c_search_fn=_search_fn([]),
        bucket_c_reconciliation_llm_fn=_c_reconciliation_fn_no_group(),
    )
    assert result["bucket"] == "C"


def test_triage_bucket_d_routes_to_bucket_d():
    result = run_pipeline(
        _CLAIM_D,
        _ALLOWLIST,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        triage_llm_fn=_triage_fn("bucket_d"),
        bucket_d_llm_fn=_d_llm_fn_not_stated(),
    )
    assert result["bucket"] == "D"
    assert result["triage_reasoning"] == "some triage reasoning"


def test_triage_ambiguous_returns_ambiguous():
    result = run_pipeline(
        _CLAIM_C,
        _ALLOWLIST,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        triage_llm_fn=_triage_fn("ambiguous", "cannot classify"),
    )
    assert result["outcome"] == "ambiguous"
    assert result["bucket"] is None
    assert result["tag"] is None
    assert result["triage_reasoning"] == "cannot classify"


def test_triage_failed_returns_triage_failed():
    def bad_triage(claim_text):
        raise RuntimeError("LLM exploded")

    result = run_pipeline(
        _CLAIM_C,
        _ALLOWLIST,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        triage_llm_fn=bad_triage,
    )
    assert result["outcome"] == "triage_failed"
    assert result["bucket"] is None
    assert result["tag"] is None
    assert result["triage_reasoning"] is None


# ---------------------------------------------------------------------------
# Explicit bucket override tests
# ---------------------------------------------------------------------------


def test_explicit_bucket_a_skips_triage():
    triage_called = {"n": 0}

    def counting_triage(claim_text):
        triage_called["n"] += 1
        return {"classification": "bucket_a", "reasoning": "r"}

    result = run_pipeline(
        _CLAIM_A,
        _ALLOWLIST,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        bucket="A",
        triage_llm_fn=counting_triage,
        extraction_search_fn=_search_fn([]),
    )
    assert triage_called["n"] == 0
    assert result["bucket"] == "A"
    assert result["triage_reasoning"] is None


def test_explicit_bucket_b_skips_triage():
    triage_called = {"n": 0}

    def counting_triage(claim_text):
        triage_called["n"] += 1
        return {"classification": "bucket_b", "reasoning": "r"}

    result = run_pipeline(
        _CLAIM_A,
        _ALLOWLIST,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        bucket="B",
        triage_llm_fn=counting_triage,
        bucket_b_search_fn=_search_fn([]),
    )
    assert triage_called["n"] == 0
    assert result["bucket"] == "B"


def test_explicit_bucket_c_skips_triage():
    triage_called = {"n": 0}

    def counting_triage(claim_text):
        triage_called["n"] += 1
        return {"classification": "bucket_c", "reasoning": "r"}

    result = run_pipeline(
        _CLAIM_C,
        _ALLOWLIST,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        bucket="C",
        triage_llm_fn=counting_triage,
        bucket_c_search_fn=_search_fn([]),
        bucket_c_reconciliation_llm_fn=_c_reconciliation_fn_no_group(),
    )
    assert triage_called["n"] == 0
    assert result["bucket"] == "C"
    assert result["triage_reasoning"] is None


def test_explicit_bucket_d_skips_triage():
    triage_called = {"n": 0}

    def counting_triage(claim_text):
        triage_called["n"] += 1
        return {"classification": "bucket_d", "reasoning": "r"}

    result = run_pipeline(
        _CLAIM_D,
        _ALLOWLIST,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        bucket="D",
        triage_llm_fn=counting_triage,
        bucket_d_llm_fn=_d_llm_fn_not_stated(),
    )
    assert triage_called["n"] == 0
    assert result["bucket"] == "D"
    assert result["triage_reasoning"] is None


def test_invalid_bucket_returns_invalid_bucket_outcome():
    result = run_pipeline(
        _CLAIM_A,
        _ALLOWLIST,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        bucket="X",
    )
    assert result["outcome"] == "invalid_bucket"
    assert result["bucket"] is None
    assert result["tag"] is None
    assert result["triage_reasoning"] is None


# ---------------------------------------------------------------------------
# Triage runs exactly once (adr/0027)
# ---------------------------------------------------------------------------


def test_triage_routed_bucket_c_never_re_triages():
    """
    When the dispatcher's own triage routes to Bucket C, the C pipeline must
    not triage the same claim a second time — a duplicate nondeterministic
    call that could contradict the routing already made (adr/0027).
    """
    triage_called = {"n": 0}

    def counting_triage(claim_text):
        triage_called["n"] += 1
        return {"classification": "bucket_c", "reasoning": "contested category"}

    result = run_pipeline(
        _CLAIM_C,
        _ALLOWLIST,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        triage_llm_fn=counting_triage,
        bucket_c_search_fn=_search_fn([]),
        bucket_c_reconciliation_llm_fn=_c_reconciliation_fn_no_group(),
    )
    assert triage_called["n"] == 1
    assert result["bucket"] == "C"
    assert result["triage_reasoning"] == "contested category"


# ---------------------------------------------------------------------------
# Search unavailability passes through as a named outcome (adr/0026)
# ---------------------------------------------------------------------------


def _unavailable_search(query):
    raise SearchUnavailable("TAVILY_API_KEY is not set")


def test_bucket_a_search_unavailable_outcome(tmp_path):
    result = run_pipeline(
        _CLAIM_A,
        _ALLOWLIST,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        bucket="A",
        extraction_search_fn=_unavailable_search,
        log_dir=str(tmp_path),
    )
    assert result["outcome"] == "search_unavailable"
    assert result["bucket"] == "A"
    assert result["tag"] is None


def test_bucket_b_search_unavailable_outcome(tmp_path):
    result = run_pipeline(
        _CLAIM_A,
        _ALLOWLIST,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        bucket="B",
        bucket_b_search_fn=_unavailable_search,
        log_dir=str(tmp_path),
    )
    assert result["outcome"] == "search_unavailable"
    assert result["bucket"] == "B"
    assert result["tag"] is None


def test_bucket_c_search_unavailable_outcome(tmp_path):
    result = run_pipeline(
        _CLAIM_C,
        _ALLOWLIST,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        bucket="C",
        bucket_c_search_fn=_unavailable_search,
        log_dir=str(tmp_path),
    )
    assert result["outcome"] == "search_unavailable"
    assert result["bucket"] == "C"
    assert result["tag"] is None


# ---------------------------------------------------------------------------
# Return shape tests
# ---------------------------------------------------------------------------

_EXPECTED_KEYS = {"outcome", "bucket", "triage_reasoning", "tag"}


def test_return_shape_always_has_four_fields():
    for b, extra in [
        ("B", {"bucket_b_search_fn": _search_fn([])}),
        ("D", {"bucket_d_llm_fn": _d_llm_fn_not_stated()}),
    ]:
        result = run_pipeline(
            _CLAIM_D,
            _ALLOWLIST,
            company_name=_COMPANY,
            claim_id=_CLAIM_ID,
            bucket=b,
            **extra,
        )
        assert (
            set(result.keys()) == _EXPECTED_KEYS
        ), f"Bucket {b} result has wrong keys: {set(result.keys())}"


def test_tag_is_claim_tag_on_bucket_b_success():
    result = run_pipeline(
        _CLAIM_A,
        _ALLOWLIST,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        bucket="B",
        bucket_b_search_fn=_search_fn(
            [{"url": _FAKE_URL, "title": "TSMC", "snippet": "net-zero"}]
        ),
        bucket_b_url_llm_fn=_b_url_llm_fn(_FAKE_URL),
        bucket_b_fetch_fn=_fetch_fn_success("TSMC has a net-zero target by 2050."),
        bucket_b_criterion_evidence_fn=_b_criterion_evidence_fn_success(),
    )
    assert isinstance(result["tag"], ClaimTag)


def test_tag_is_claim_tag_on_bucket_d_success():
    result = run_pipeline(
        _CLAIM_D,
        _ALLOWLIST,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        bucket="D",
        bucket_d_llm_fn=_d_llm_fn_explicit(),
    )
    assert isinstance(result["tag"], ClaimTag)


def test_tag_is_none_on_ambiguous():
    result = run_pipeline(
        _CLAIM_C,
        _ALLOWLIST,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        triage_llm_fn=_triage_fn("ambiguous"),
    )
    assert result["tag"] is None


# ---------------------------------------------------------------------------
# Outcome string tests
# ---------------------------------------------------------------------------


def test_bucket_b_outcome_is_criteria_evidence_gathered():
    result = run_pipeline(
        _CLAIM_A,
        _ALLOWLIST,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        bucket="B",
        bucket_b_search_fn=_search_fn(
            [{"url": _FAKE_URL, "title": "TSMC", "snippet": "net-zero"}]
        ),
        bucket_b_url_llm_fn=_b_url_llm_fn(_FAKE_URL),
        bucket_b_fetch_fn=_fetch_fn_success("TSMC has a net-zero target by 2050."),
        bucket_b_criterion_evidence_fn=_b_criterion_evidence_fn_success(),
    )
    assert result["outcome"] == "criteria_evidence_gathered"


def test_bucket_b_outcome_is_incomplete_when_nothing_gathered():
    result = run_pipeline(
        _CLAIM_A,
        _ALLOWLIST,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        bucket="B",
        bucket_b_search_fn=_search_fn([]),
    )
    assert result["outcome"] == "incomplete"


def test_bucket_d_outcome_is_assumptions_explicit():
    result = run_pipeline(
        _CLAIM_D,
        _ALLOWLIST,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        bucket="D",
        bucket_d_llm_fn=_d_llm_fn_explicit(),
    )
    assert result["outcome"] == "assumptions_explicit"


def test_bucket_d_outcome_is_assumptions_not_stated():
    result = run_pipeline(
        _CLAIM_D,
        _ALLOWLIST,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        bucket="D",
        bucket_d_llm_fn=_d_llm_fn_not_stated(),
    )
    assert result["outcome"] == "assumptions_not_stated"


def test_bucket_c_outcome_is_disambiguated():
    _url_a = _FAKE_URL_2
    _url_b = "https://trendforce.com/foundry"

    call_count = {"n": 0}

    def alternating_url_llm(claim_text, search_results):
        call_count["n"] += 1
        return {"url": _url_a if call_count["n"] % 2 == 1 else _url_b}

    def group_reconcile(claim_text, findings, feedback):
        urls = [f["source_url"] for f in findings if f.get("source_url")]
        if len(urls) >= 2:
            return {
                "groups": [
                    {
                        "member_source_urls": [urls[0], urls[1]],
                        "shared_definition_label": "pure-play foundry market",
                        "reasoning": "Same scope.",
                    }
                ],
                "distinct": [],
                "unresolved": [],
            }
        return {"groups": [], "distinct": [], "unresolved": []}

    result = run_pipeline(
        _CLAIM_C,
        _ALLOWLIST,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        bucket="C",
        bucket_c_search_fn=_search_fn(_FAKE_C_SEARCH),
        bucket_c_url_llm_fn=alternating_url_llm,
        bucket_c_fetch_fn=_fetch_fn_success(_FAKE_DOCUMENT_C),
        bucket_c_finding_llm_fn=_c_finding_llm_fn(),
        bucket_c_reconciliation_llm_fn=group_reconcile,
    )
    assert result["outcome"] == "disambiguated"


def test_bucket_c_outcome_is_definitional_ambiguity_unresolved():
    result = run_pipeline(
        _CLAIM_C,
        _ALLOWLIST,
        company_name=_COMPANY,
        claim_id=_CLAIM_ID,
        bucket="C",
        bucket_c_search_fn=_search_fn([]),
        bucket_c_reconciliation_llm_fn=_c_reconciliation_fn_no_group(),
    )
    assert result["outcome"] == "definitional_ambiguity_unresolved"


# ---------------------------------------------------------------------------
# Public interface tests
# ---------------------------------------------------------------------------


def test_extraction_default_llm_call_is_publicly_importable():
    from agent_eval.extraction import default_llm_call

    assert callable(default_llm_call)


# ---------------------------------------------------------------------------
# Live API tests (opt-in)
# ---------------------------------------------------------------------------


@pytest.mark.live_api
@pytest.mark.skipif(
    not os.getenv("RUN_LIVE_API"),
    reason="live API test; set RUN_LIVE_API=1 to run deliberately (costs money)",
)
def test_live_run_pipeline_bucket_c_tsmc_foundry_market():
    """
    No injected fakes. Real triage + real Bucket C pipeline.
    Both "disambiguated" and "definitional_ambiguity_unresolved" are valid
    outcomes — do NOT assert a specific one. Requires OPENAI_API_KEY and
    TAVILY_API_KEY in the environment.
    """
    _valid_c_outcomes = {"disambiguated", "definitional_ambiguity_unresolved"}

    result = run_pipeline(
        "TSMC has roughly 60% of the foundry market",
        ["tsmc.com"],
        company_name="TSMC",
        claim_id="tsmc-dispatcher-live-c-001",
    )

    assert result["bucket"] == "C", f"Expected bucket='C'. Full result: {result}"
    assert result["outcome"] in _valid_c_outcomes, (
        f"outcome must be one of {_valid_c_outcomes}, got {result['outcome']!r}. "
        f"Full result: {result}"
    )
    assert (
        isinstance(result["triage_reasoning"], str) and result["triage_reasoning"]
    ), f"triage_reasoning must be a non-empty string. Full result: {result}"
    tag = result["tag"]
    assert tag is None or isinstance(
        tag, ClaimTag
    ), f"tag must be ClaimTag or None. Full result: {result}"


@pytest.mark.live_api
@pytest.mark.skipif(
    not os.getenv("RUN_LIVE_API"),
    reason="live API test; set RUN_LIVE_API=1 to run deliberately (costs money)",
)
def test_live_run_pipeline_bucket_d_explicit():
    """
    bucket='D' explicitly supplied — no triage. Real Bucket D pipeline.
    Requires OPENAI_API_KEY in the environment.
    """
    result = run_pipeline(
        _CLAIM_D,
        _ALLOWLIST,
        company_name=_COMPANY,
        claim_id="tsmc-dispatcher-live-d-001",
        bucket="D",
    )

    assert result["bucket"] == "D", f"Expected bucket='D'. Full result: {result}"
    assert (
        result["triage_reasoning"] is None
    ), f"triage_reasoning must be None for explicit routing. Full result: {result}"
    assert isinstance(
        result["tag"], ClaimTag
    ), f"tag must be a ClaimTag. Full result: {result}"


# ---------------------------------------------------------------------------
# Redirect re-validation through the dispatcher (adr/0023)
# ---------------------------------------------------------------------------


def test_bucket_a_off_allowlist_redirect_is_unverifiable(tmp_path):
    """
    Through the dispatcher, a proposed on-allowlist URL whose fetch reports
    an off-domain final_url must not produce a verified tag
    (adr/0023-redirect-revalidation.md).
    """
    document = (
        "TSMC announced it is moving its target for 100 percent renewable "
        "energy consumption for all global operations forward to 2040 from 2050."
    )
    quote = (
        "moving its target for 100 percent renewable energy consumption "
        "for all global operations forward to 2040 from 2050"
    )
    url = "https://pr.tsmc.com/english/news/3067"

    result = run_pipeline(
        "TSMC is moving its 100 percent renewable target to 2040 from 2050",
        allowlist=["tsmc.com", "pr.tsmc.com"],
        company_name="TSMC",
        claim_id="tsmc-a-redirect",
        bucket="A",
        extraction_llm_fn=lambda ct, fb, sr: {"url": url, "quote": quote},
        extraction_search_fn=lambda q: [
            {"url": url, "title": "TSMC", "snippet": "..."}
        ],
        extraction_fetch_fn=lambda u: {
            "success": True,
            "text": document,
            "content_type": "text/html",
            "failure_reason": None,
            "final_url": "https://evil.example/cached-copy",
        },
        log_dir=str(tmp_path),
    )
    assert result["outcome"] == "unverifiable"
    assert result["tag"] is None


def test_bucket_a_same_domain_redirect_tag_reflects_final_url(tmp_path):
    """
    On success, the rebuilt ClaimTag's domain evidence describes the
    post-redirect URL — the document that was actually checked.
    """
    document = (
        "TSMC announced it is moving its target for 100 percent renewable "
        "energy consumption for all global operations forward to 2040 from 2050."
    )
    quote = (
        "moving its target for 100 percent renewable energy consumption "
        "for all global operations forward to 2040 from 2050"
    )
    url = "https://pr.tsmc.com/english/news/3067"

    result = run_pipeline(
        "TSMC is moving its 100 percent renewable target to 2040 from 2050",
        allowlist=["tsmc.com", "pr.tsmc.com"],
        company_name="TSMC",
        claim_id="tsmc-a-redirect-ok",
        bucket="A",
        extraction_llm_fn=lambda ct, fb, sr: {"url": url, "quote": quote},
        extraction_search_fn=lambda q: [
            {"url": url, "title": "TSMC", "snippet": "..."}
        ],
        extraction_fetch_fn=lambda u: {
            "success": True,
            "text": document,
            "content_type": "text/html",
            "failure_reason": None,
            "final_url": "https://www.tsmc.com/english/news/3067-moved",
        },
        log_dir=str(tmp_path),
    )
    assert result["outcome"] == "verified"
    assert result["tag"] is not None
    assert result["tag"].domain_evidence.domain == "tsmc.com"
