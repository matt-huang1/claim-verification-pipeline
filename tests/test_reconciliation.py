"""
Tests for reconciliation.py.

All unit tests inject a fake llm_fn — no real API calls. One live test
(opt-in via RUN_LIVE_API=1) uses the four worked-example definitions from
the system prompt to verify the model routes A+B to a group, C to distinct,
and D to unresolved.
"""

import os

import pytest

from reconciliation import (
    _DUPLICATE_URL_FEEDBACK,
    _HALLUCINATED_URL_FEEDBACK,
    _MALFORMED_JSON_FEEDBACK,
    _MISSING_REASONING_FEEDBACK,
    _MISSING_URL_FEEDBACK,
    _SINGLE_MEMBER_GROUP_FEEDBACK,
    _SOLE_SOURCE_REASONING,
    reconcile_sources,
)
from tag_schema import (
    DefinitionGroup,
    DistinctFinding,
    SourceFinding,
    UnresolvedFinding,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _finding(
    url: str,
    definition_found: bool = True,
    definition_text: str | None = None,
    claimed_value: str | None = None,
) -> SourceFinding:
    return SourceFinding(
        source_url=url,
        source_type="third_party",
        value_found=claimed_value is not None,
        claimed_value=claimed_value,
        is_literal_value=False,
        value_verification_status=None,
        definition_found=definition_found,
        definition_text=(
            definition_text
            if definition_text is not None
            else ("some definition" if definition_found else None)
        ),
        definition_verification_status="unique" if definition_found else None,
    )


_URL_A = "https://trendforce.com/report"
_URL_B = "https://counterpoint.com/report"
_URL_C = "https://idc.com/report"
_URL_D = "https://vague-source.com/report"
_URL_NO_DEF = "https://nodefinition.com/report"


def _well_formed_response(groups=None, distinct=None, unresolved=None) -> dict:
    return {
        "groups": groups or [],
        "distinct": distinct or [],
        "unresolved": unresolved or [],
    }


# ---------------------------------------------------------------------------
# Deterministic split — no llm_fn needed
# ---------------------------------------------------------------------------


def test_zero_definition_bearing_findings_returns_empty_no_llm_call():
    """0 definition-bearing findings: groups/distinct/unresolved/failed all empty."""
    called = {"n": 0}

    def should_not_be_called(*args, **kwargs):
        called["n"] += 1
        return {}

    result = reconcile_sources(
        claim_text="TSMC market share",
        findings=[
            _finding(_URL_NO_DEF, definition_found=False),
            _finding("https://other.com/report", definition_found=False),
        ],
        llm_fn=should_not_be_called,
    )

    assert called["n"] == 0
    assert result.groups == []
    assert result.distinct_sources == []
    assert result.unresolved == []
    assert result.failed_reconciliation == []
    assert set(result.no_definition_sources) == {
        _URL_NO_DEF,
        "https://other.com/report",
    }
    assert result.sources_checked == 2


def test_one_definition_bearing_finding_goes_to_distinct_no_llm_call():
    """1 definition-bearing finding → distinct with hardcoded reasoning, no LLM call."""
    called = {"n": 0}

    def should_not_be_called(*args, **kwargs):
        called["n"] += 1
        return {}

    result = reconcile_sources(
        claim_text="TSMC market share",
        findings=[
            _finding(_URL_A),
            _finding(_URL_NO_DEF, definition_found=False),
        ],
        llm_fn=should_not_be_called,
    )

    assert called["n"] == 0
    assert len(result.distinct_sources) == 1
    assert result.distinct_sources[0].source_url == _URL_A
    assert result.distinct_sources[0].reasoning == _SOLE_SOURCE_REASONING
    assert result.groups == []
    assert result.unresolved == []
    assert result.failed_reconciliation == []
    assert result.no_definition_sources == [_URL_NO_DEF]


def test_two_definition_bearing_findings_triggers_llm_call():
    """2+ definition-bearing findings: LLM IS called."""
    called = {"n": 0}

    def counting_fn(claim_text, findings, feedback):
        called["n"] += 1
        return _well_formed_response(
            distinct=[
                {"source_url": _URL_A, "reasoning": "different scope"},
                {"source_url": _URL_B, "reasoning": "different scope too"},
            ]
        )

    reconcile_sources(
        claim_text="TSMC market share",
        findings=[_finding(_URL_A), _finding(_URL_B)],
        llm_fn=counting_fn,
    )
    assert called["n"] >= 1


# ---------------------------------------------------------------------------
# Well-formed first-attempt responses
# ---------------------------------------------------------------------------


def test_all_three_output_types_mapped_correctly():
    """groups + distinct + unresolved all populated correctly on first attempt."""
    result = reconcile_sources(
        claim_text="TSMC market share",
        findings=[
            _finding(_URL_A),
            _finding(_URL_B),
            _finding(_URL_C),
            _finding(_URL_D),
        ],
        llm_fn=lambda ct, fs, fb: _well_formed_response(
            groups=[
                {
                    "member_source_urls": [_URL_A, _URL_B],
                    "shared_definition_label": "pure-play excl. IDM",
                    "reasoning": "both exclude IDM captive",
                }
            ],
            distinct=[{"source_url": _URL_C, "reasoning": "includes IDMs"}],
            unresolved=[{"source_url": _URL_D, "reasoning": "too vague"}],
        ),
    )

    assert len(result.groups) == 1
    assert isinstance(result.groups[0], DefinitionGroup)
    assert set(result.groups[0].member_source_urls) == {_URL_A, _URL_B}
    assert result.groups[0].shared_definition_label == "pure-play excl. IDM"
    assert result.groups[0].reasoning == "both exclude IDM captive"

    assert len(result.distinct_sources) == 1
    assert isinstance(result.distinct_sources[0], DistinctFinding)
    assert result.distinct_sources[0].source_url == _URL_C

    assert len(result.unresolved) == 1
    assert isinstance(result.unresolved[0], UnresolvedFinding)
    assert result.unresolved[0].source_url == _URL_D

    assert result.failed_reconciliation == []


def test_all_grouped_into_one_group():
    """All sources grouped → disambiguated (via overall_status)."""
    result = reconcile_sources(
        claim_text="TSMC market share",
        findings=[_finding(_URL_A), _finding(_URL_B)],
        llm_fn=lambda ct, fs, fb: _well_formed_response(
            groups=[
                {
                    "member_source_urls": [_URL_A, _URL_B],
                    "shared_definition_label": "same scope",
                    "reasoning": "share the same boundary",
                }
            ]
        ),
    )
    assert len(result.groups) == 1
    assert result.failed_reconciliation == []


def test_all_sources_unresolved():
    """All sources unresolved on first attempt — accepted as-is, no retry."""
    call_count = {"n": 0}

    def fn(ct, fs, fb):
        call_count["n"] += 1
        return _well_formed_response(
            unresolved=[
                {"source_url": _URL_A, "reasoning": "too vague"},
                {"source_url": _URL_B, "reasoning": "also too vague"},
            ]
        )

    result = reconcile_sources(
        claim_text="TSMC market share",
        findings=[_finding(_URL_A), _finding(_URL_B)],
        llm_fn=fn,
    )
    assert call_count["n"] == 1  # well-formed judgment, never retried
    assert len(result.unresolved) == 2
    assert result.groups == []
    assert result.failed_reconciliation == []


def test_all_sources_distinct():
    """All sources in distinct — groups is empty."""
    result = reconcile_sources(
        claim_text="TSMC market share",
        findings=[_finding(_URL_A), _finding(_URL_B)],
        llm_fn=lambda ct, fs, fb: _well_formed_response(
            distinct=[
                {"source_url": _URL_A, "reasoning": "different scope A"},
                {"source_url": _URL_B, "reasoning": "different scope B"},
            ]
        ),
    )
    assert result.groups == []
    assert len(result.distinct_sources) == 2
    assert result.failed_reconciliation == []


# ---------------------------------------------------------------------------
# Retry logic
# ---------------------------------------------------------------------------


def test_malformed_attempt_1_wellformed_attempt_2_uses_attempt_2():
    """Malformed first attempt → retry → well-formed second attempt is accepted."""
    call_count = {"n": 0}

    def fn(ct, fs, fb):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return {"bad": "response"}  # missing groups/distinct/unresolved keys,
            # but _validate will catch missing URLs
        return _well_formed_response(
            distinct=[
                {"source_url": _URL_A, "reasoning": "ok"},
                {"source_url": _URL_B, "reasoning": "ok"},
            ]
        )

    result = reconcile_sources(
        claim_text="TSMC market share",
        findings=[_finding(_URL_A), _finding(_URL_B)],
        llm_fn=fn,
    )
    assert call_count["n"] == 2
    assert result.failed_reconciliation == []
    assert len(result.distinct_sources) == 2


def test_both_attempts_malformed_all_go_to_failed_reconciliation():
    """Both attempts malformed → all definition-bearing sources in failed_reconciliation."""
    call_count = {"n": 0}

    def always_bad(ct, fs, fb):
        call_count["n"] += 1
        return {}  # missing every expected URL

    result = reconcile_sources(
        claim_text="TSMC market share",
        findings=[_finding(_URL_A), _finding(_URL_B)],
        llm_fn=always_bad,
    )
    assert call_count["n"] == 2
    assert set(result.failed_reconciliation) == {_URL_A, _URL_B}
    assert result.groups == []
    assert result.distinct_sources == []
    assert result.unresolved == []


def test_all_unresolved_on_attempt_1_not_retried():
    """'all unresolved' is a genuine judgment outcome — llm_fn called exactly once."""
    call_count = {"n": 0}

    def fn(ct, fs, fb):
        call_count["n"] += 1
        return _well_formed_response(
            unresolved=[
                {"source_url": _URL_A, "reasoning": "vague"},
                {"source_url": _URL_B, "reasoning": "vague too"},
            ]
        )

    reconcile_sources(
        claim_text="TSMC market share",
        findings=[_finding(_URL_A), _finding(_URL_B)],
        llm_fn=fn,
    )
    assert call_count["n"] == 1


# ---------------------------------------------------------------------------
# Malformed response detection — each defect type
# ---------------------------------------------------------------------------


def _assert_triggers_failed_reconciliation(llm_fn):
    """Helper: assert that the given llm_fn eventually causes failed_reconciliation."""
    result = reconcile_sources(
        claim_text="TSMC market share",
        findings=[_finding(_URL_A), _finding(_URL_B)],
        llm_fn=llm_fn,
    )
    assert set(result.failed_reconciliation) == {_URL_A, _URL_B}
    assert result.groups == []
    assert result.distinct_sources == []
    assert result.unresolved == []


def test_hallucinated_url_triggers_failed_reconciliation():
    feedback_seen = {}

    def fn(ct, fs, fb):
        if fb:
            feedback_seen["fb"] = fb
        return _well_formed_response(
            distinct=[
                {"source_url": "https://hallucinated.com", "reasoning": "invented"},
                {"source_url": _URL_B, "reasoning": "real"},
            ]
        )

    _assert_triggers_failed_reconciliation(fn)
    assert feedback_seen.get("fb") == _HALLUCINATED_URL_FEEDBACK


def test_missing_url_triggers_failed_reconciliation():
    feedback_seen = {}

    def fn(ct, fs, fb):
        if fb:
            feedback_seen["fb"] = fb
        # Only returns _URL_A, drops _URL_B
        return _well_formed_response(
            distinct=[{"source_url": _URL_A, "reasoning": "ok"}]
        )

    _assert_triggers_failed_reconciliation(fn)
    assert feedback_seen.get("fb") == _MISSING_URL_FEEDBACK


def test_duplicate_url_triggers_failed_reconciliation():
    feedback_seen = {}

    def fn(ct, fs, fb):
        if fb:
            feedback_seen["fb"] = fb
        return _well_formed_response(
            distinct=[
                {"source_url": _URL_A, "reasoning": "ok"},
                {"source_url": _URL_A, "reasoning": "duplicate"},  # _URL_B missing
            ]
        )

    _assert_triggers_failed_reconciliation(fn)
    assert feedback_seen.get("fb") == _DUPLICATE_URL_FEEDBACK


def test_single_member_group_triggers_failed_reconciliation():
    feedback_seen = {}

    def fn(ct, fs, fb):
        if fb:
            feedback_seen["fb"] = fb
        return _well_formed_response(
            groups=[
                {
                    "member_source_urls": [_URL_A],  # only 1 member
                    "shared_definition_label": "x",
                    "reasoning": "singleton group",
                }
            ],
            distinct=[{"source_url": _URL_B, "reasoning": "ok"}],
        )

    _assert_triggers_failed_reconciliation(fn)
    assert feedback_seen.get("fb") == _SINGLE_MEMBER_GROUP_FEEDBACK


def test_missing_reasoning_on_group_triggers_failed_reconciliation():
    feedback_seen = {}

    def fn(ct, fs, fb):
        if fb:
            feedback_seen["fb"] = fb
        return _well_formed_response(
            groups=[
                {
                    "member_source_urls": [_URL_A, _URL_B],
                    "shared_definition_label": "x",
                    "reasoning": "",  # empty
                }
            ]
        )

    _assert_triggers_failed_reconciliation(fn)
    assert feedback_seen.get("fb") == _MISSING_REASONING_FEEDBACK


def test_missing_reasoning_on_distinct_triggers_failed_reconciliation():
    feedback_seen = {}

    def fn(ct, fs, fb):
        if fb:
            feedback_seen["fb"] = fb
        return _well_formed_response(
            distinct=[
                {"source_url": _URL_A, "reasoning": ""},  # empty
                {"source_url": _URL_B, "reasoning": "ok"},
            ]
        )

    _assert_triggers_failed_reconciliation(fn)
    assert feedback_seen.get("fb") == _MISSING_REASONING_FEEDBACK


def test_missing_reasoning_on_unresolved_triggers_failed_reconciliation():
    feedback_seen = {}

    def fn(ct, fs, fb):
        if fb:
            feedback_seen["fb"] = fb
        return _well_formed_response(
            unresolved=[
                {"source_url": _URL_A, "reasoning": ""},  # empty
                {"source_url": _URL_B, "reasoning": "ok"},
            ]
        )

    _assert_triggers_failed_reconciliation(fn)
    assert feedback_seen.get("fb") == _MISSING_REASONING_FEEDBACK


def test_unparseable_json_triggers_failed_reconciliation():
    feedback_seen = {}

    def fn(ct, fs, fb):
        if fb:
            feedback_seen["fb"] = fb
        raise ValueError("simulated JSON parse error")

    _assert_triggers_failed_reconciliation(fn)
    assert feedback_seen.get("fb") == _MALFORMED_JSON_FEEDBACK


# ---------------------------------------------------------------------------
# Live test — opt-in only, costs money
# ---------------------------------------------------------------------------

# The four worked-example sources from the system prompt.
# These are passed as SourceFinding stubs; we only need definition_text
# and source_url to drive reconciliation.
_LIVE_SOURCE_A = "https://source-a.example.com/pure-play-excl-idm"
_LIVE_SOURCE_B = "https://source-b.example.com/merchant-excl-captive"
_LIVE_SOURCE_C = "https://source-c.example.com/total-incl-idm"
_LIVE_SOURCE_D = "https://source-d.example.com/vague-foundry"


@pytest.mark.live_api
@pytest.mark.skipif(
    not os.getenv("RUN_LIVE_API"),
    reason="live API test; set RUN_LIVE_API=1 to run deliberately (costs money)",
)
def test_live_reconcile_worked_examples():
    """
    Passes the four worked-example definitions from the system prompt to the
    real model (no injected llm_fn) and asserts:
      - A and B end up in the same group
      - C ends up in distinct_sources
      - D ends up in unresolved

    Failure messages include the actual result so a regression is immediately
    diagnosable without re-running.
    """
    findings = [
        _finding(
            _LIVE_SOURCE_A,
            definition_text=(
                "pure-play foundry market, excluding IDMs' in-house "
                "fabrication (Samsung, Intel)"
            ),
        ),
        _finding(
            _LIVE_SOURCE_B,
            definition_text=(
                "merchant foundry market, excluding integrated device "
                "manufacturers' captive capacity"
            ),
        ),
        _finding(
            _LIVE_SOURCE_C,
            definition_text=(
                "total semiconductor manufacturing capacity, including "
                "in-house IDM fabrication"
            ),
        ),
        _finding(
            _LIVE_SOURCE_D,
            definition_text="the foundry market",
        ),
    ]

    result = reconcile_sources(
        claim_text="TSMC has roughly 60% of the foundry market",
        findings=findings,
    )

    # A and B should be grouped together
    ab_grouped = any(
        {_LIVE_SOURCE_A, _LIVE_SOURCE_B}.issubset(set(g.member_source_urls))
        for g in result.groups
    )
    assert ab_grouped, (
        f"Expected sources A and B in the same group.\n"
        f"groups={result.groups}\n"
        f"distinct={result.distinct_sources}\n"
        f"unresolved={result.unresolved}\n"
        f"failed={result.failed_reconciliation}"
    )

    # C should be in distinct_sources
    c_distinct = any(d.source_url == _LIVE_SOURCE_C for d in result.distinct_sources)
    assert c_distinct, (
        f"Expected source C in distinct_sources.\n"
        f"groups={result.groups}\n"
        f"distinct={result.distinct_sources}\n"
        f"unresolved={result.unresolved}\n"
        f"failed={result.failed_reconciliation}"
    )

    # D should be in unresolved
    d_unresolved = any(u.source_url == _LIVE_SOURCE_D for u in result.unresolved)
    assert d_unresolved, (
        f"Expected source D in unresolved.\n"
        f"groups={result.groups}\n"
        f"distinct={result.distinct_sources}\n"
        f"unresolved={result.unresolved}\n"
        f"failed={result.failed_reconciliation}"
    )

    # Nothing in failed_reconciliation — the response should be well-formed
    assert (
        result.failed_reconciliation == []
    ), f"Unexpected failed_reconciliation: {result.failed_reconciliation}"
