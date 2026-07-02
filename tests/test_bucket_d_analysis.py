"""
Tests for bucket_d_analysis.py.

All unit tests inject a fake llm_fn — no real API calls. One live test
(opt-in via RUN_LIVE_API=1) uses the worked-example claim from the system
prompt to verify the model surfaces real content.
"""

import json
import os

import pytest

from bucket_d_analysis import (
    _INVALID_ITEM_FEEDBACK,
    _MALFORMED_JSON_FEEDBACK,
    _MISSING_FIELDS_FEEDBACK,
    analyze_assumptions,
)
from log_utils import LOG_FILENAME
from tag_schema import AssumptionsStatedEvidence

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_CLAIM = (
    "Without TSMC, the global climate transition would be set back "
    "by a decade because advanced chips are essential for clean "
    "energy technology."
)
_COMPANY = "TSMC"


def _make_llm_fn(assumptions=None, causal_steps=None):
    """Returns a well-formed llm_fn with the given items."""
    if assumptions is None:
        assumptions = [
            {
                "text": "Advanced chips are essential for clean energy technology",
                "present_in_claim": True,
            },
            {
                "text": "No other fab can substitute for TSMC at scale",
                "present_in_claim": False,
            },
        ]
    if causal_steps is None:
        causal_steps = [
            {
                "text": "No advanced chips → clean energy technology deployment slows",
                "present_in_claim": True,
            },
            {
                "text": "TSMC absence → no advanced chip supply at scale",
                "present_in_claim": False,
            },
        ]

    _a = assumptions
    _cs = causal_steps

    def fn(claim_text, feedback):
        return {"assumptions": _a, "causal_steps": _cs}

    return fn


def _always_bad_fn(claim_text, feedback):
    """Always returns a structurally invalid response."""
    return {"bad": "response"}


def _read_log(tmp_path):
    path = tmp_path / LOG_FILENAME
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Well-formed first-attempt tests
# ---------------------------------------------------------------------------


def test_well_formed_response_builds_correct_evidence():
    """Both assumptions and causal_steps populated. Assert fields match exactly."""
    result = analyze_assumptions(
        _CLAIM,
        company_name=_COMPANY,
        llm_fn=_make_llm_fn(
            assumptions=[
                {"text": "Stated assumption", "present_in_claim": True},
                {"text": "Unstated assumption", "present_in_claim": False},
            ],
            causal_steps=[
                {"text": "Stated step", "present_in_claim": True},
            ],
        ),
    )

    assert isinstance(result, AssumptionsStatedEvidence)
    assert len(result.assumptions) == 2
    assert result.assumptions[0].text == "Stated assumption"
    assert result.assumptions[0].present_in_claim is True
    assert result.assumptions[1].text == "Unstated assumption"
    assert result.assumptions[1].present_in_claim is False
    assert len(result.causal_steps) == 1
    assert result.causal_steps[0].text == "Stated step"
    assert result.causal_steps[0].present_in_claim is True


def test_all_unstated_items_still_accepted_not_retried():
    """Well-formed response where every present_in_claim=False. Called exactly once."""
    call_count = {"n": 0}

    def fn(claim_text, feedback):
        call_count["n"] += 1
        return {
            "assumptions": [{"text": "Missing premise", "present_in_claim": False}],
            "causal_steps": [{"text": "Leap of logic", "present_in_claim": False}],
        }

    result = analyze_assumptions(_CLAIM, company_name=_COMPANY, llm_fn=fn)

    assert call_count["n"] == 1
    assert result.assumptions[0].present_in_claim is False
    assert result.causal_steps[0].present_in_claim is False


def test_empty_lists_accepted_not_retried():
    """Well-formed response with assumptions=[], causal_steps=[]. Called exactly once."""
    call_count = {"n": 0}

    def fn(claim_text, feedback):
        call_count["n"] += 1
        return {"assumptions": [], "causal_steps": []}

    result = analyze_assumptions(_CLAIM, company_name=_COMPANY, llm_fn=fn)

    assert call_count["n"] == 1
    assert result.assumptions == []
    assert result.causal_steps == []


def test_notes_field_is_empty_string_not_llm_populated():
    """The LLM never populates notes — it must be empty on a normal successful run."""
    result = analyze_assumptions(_CLAIM, company_name=_COMPANY, llm_fn=_make_llm_fn())
    assert result.notes == ""


# ---------------------------------------------------------------------------
# Retry tests
# ---------------------------------------------------------------------------


def test_malformed_attempt_1_wellformed_attempt_2_uses_attempt_2():
    """Malformed first attempt → retry → well-formed second attempt is accepted."""
    call_count = {"n": 0}

    def fn(claim_text, feedback):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return {"bad": "response"}
        return {
            "assumptions": [{"text": "Recovered assumption", "present_in_claim": True}],
            "causal_steps": [{"text": "Recovered step", "present_in_claim": True}],
        }

    result = analyze_assumptions(_CLAIM, company_name=_COMPANY, llm_fn=fn)

    assert call_count["n"] == 2
    assert len(result.assumptions) == 1
    assert result.assumptions[0].text == "Recovered assumption"


def test_both_attempts_malformed_returns_empty_evidence_with_notes():
    """Both attempts malformed → empty lists, notes contains 'failed'."""
    call_count = {"n": 0}

    def fn(claim_text, feedback):
        call_count["n"] += 1
        return {"bad": "response"}

    result = analyze_assumptions(_CLAIM, company_name=_COMPANY, llm_fn=fn)

    assert call_count["n"] == 2
    assert result.assumptions == []
    assert result.causal_steps == []
    assert "failed" in result.notes.lower()


def test_wellformed_all_unstated_not_retried():
    """'all present_in_claim=False' is a genuine finding — llm_fn called once."""
    call_count = {"n": 0}

    def fn(claim_text, feedback):
        call_count["n"] += 1
        return {
            "assumptions": [{"text": "A", "present_in_claim": False}],
            "causal_steps": [{"text": "B", "present_in_claim": False}],
        }

    analyze_assumptions(_CLAIM, company_name=_COMPANY, llm_fn=fn)
    assert call_count["n"] == 1


# ---------------------------------------------------------------------------
# Malformed-response tests — each defect type
# ---------------------------------------------------------------------------


def _assert_feedback_on_retry(bad_fn, expected_feedback):
    """
    Helper: run analyze_assumptions with a fn that always returns bad_fn's output
    on attempt 1 and records the feedback on attempt 2. Asserts the expected
    feedback was passed.
    """
    feedback_seen = {}
    call_count = {"n": 0}

    def fn(claim_text, feedback):
        call_count["n"] += 1
        if feedback is not None:
            feedback_seen["fb"] = feedback
        return bad_fn(claim_text, feedback)

    analyze_assumptions(_CLAIM, company_name=_COMPANY, llm_fn=fn)
    assert feedback_seen.get("fb") == expected_feedback


def test_malformed_json_triggers_retry():
    """llm_fn raises an exception → _MALFORMED_JSON_FEEDBACK on retry."""

    def raising_fn(claim_text, feedback):
        raise ValueError("simulated JSON parse error")

    _assert_feedback_on_retry(raising_fn, _MALFORMED_JSON_FEEDBACK)


def test_missing_assumptions_field_triggers_retry():
    """Response has only 'causal_steps' → _MISSING_FIELDS_FEEDBACK on retry."""

    def fn(claim_text, feedback):
        return {"causal_steps": []}

    _assert_feedback_on_retry(fn, _MISSING_FIELDS_FEEDBACK)


def test_missing_causal_steps_field_triggers_retry():
    """Response has only 'assumptions' → _MISSING_FIELDS_FEEDBACK on retry."""

    def fn(claim_text, feedback):
        return {"assumptions": []}

    _assert_feedback_on_retry(fn, _MISSING_FIELDS_FEEDBACK)


def test_assumptions_not_a_list_triggers_retry():
    """assumptions is a string, not a list → _MISSING_FIELDS_FEEDBACK on retry."""

    def fn(claim_text, feedback):
        return {"assumptions": "not a list", "causal_steps": []}

    _assert_feedback_on_retry(fn, _MISSING_FIELDS_FEEDBACK)


def test_empty_text_on_assumption_triggers_retry():
    """Item with text="" → _INVALID_ITEM_FEEDBACK on retry."""

    def fn(claim_text, feedback):
        return {
            "assumptions": [{"text": "", "present_in_claim": True}],
            "causal_steps": [],
        }

    _assert_feedback_on_retry(fn, _INVALID_ITEM_FEEDBACK)


def test_string_present_in_claim_triggers_retry():
    """present_in_claim="true" (string) → _INVALID_ITEM_FEEDBACK on retry."""

    def fn(claim_text, feedback):
        return {
            "assumptions": [{"text": "A premise", "present_in_claim": "true"}],
            "causal_steps": [],
        }

    _assert_feedback_on_retry(fn, _INVALID_ITEM_FEEDBACK)


def test_integer_present_in_claim_triggers_retry():
    """present_in_claim=1 (integer) → _INVALID_ITEM_FEEDBACK on retry."""

    def fn(claim_text, feedback):
        return {
            "assumptions": [{"text": "A premise", "present_in_claim": 1}],
            "causal_steps": [],
        }

    _assert_feedback_on_retry(fn, _INVALID_ITEM_FEEDBACK)


def test_missing_present_in_claim_triggers_retry():
    """Item missing present_in_claim entirely → _INVALID_ITEM_FEEDBACK on retry."""

    def fn(claim_text, feedback):
        return {
            "assumptions": [{"text": "A premise"}],
            "causal_steps": [],
        }

    _assert_feedback_on_retry(fn, _INVALID_ITEM_FEEDBACK)


def test_missing_text_triggers_retry():
    """Item missing 'text' entirely → _INVALID_ITEM_FEEDBACK on retry."""

    def fn(claim_text, feedback):
        return {
            "assumptions": [{"present_in_claim": True}],
            "causal_steps": [],
        }

    _assert_feedback_on_retry(fn, _INVALID_ITEM_FEEDBACK)


# ---------------------------------------------------------------------------
# Logging tests
# ---------------------------------------------------------------------------


def test_log_entry_for_analyzed_outcome(tmp_path):
    """
    Well-formed first attempt. Assert every field in the log entry:
    bucket="D", company_name, claim_text, assumptions_found,
    causal_steps_found, stated_assumptions_count, stated_steps_count,
    attempts=1, outcome="analyzed".
    """
    analyze_assumptions(
        _CLAIM,
        company_name=_COMPANY,
        llm_fn=_make_llm_fn(
            assumptions=[
                {"text": "Stated A", "present_in_claim": True},
                {"text": "Unstated B", "present_in_claim": False},
            ],
            causal_steps=[
                {"text": "Stated step", "present_in_claim": True},
                {"text": "Missing step", "present_in_claim": False},
            ],
        ),
        log_dir=str(tmp_path),
    )

    entries = _read_log(tmp_path)
    assert len(entries) == 1
    e = entries[0]
    assert e["bucket"] == "D"
    assert e["company_name"] == _COMPANY
    assert e["claim_text"] == _CLAIM
    assert e["assumptions_found"] == 2
    assert e["causal_steps_found"] == 2
    assert e["stated_assumptions_count"] == 1
    assert e["stated_steps_count"] == 1
    assert e["attempts"] == 1
    assert e["outcome"] == "analyzed"


def test_log_entry_for_failed_outcome(tmp_path):
    """Both attempts malformed. Assert outcome='failed', attempts=2, counts=0."""
    analyze_assumptions(
        _CLAIM,
        company_name=_COMPANY,
        llm_fn=_always_bad_fn,
        log_dir=str(tmp_path),
    )

    entries = _read_log(tmp_path)
    assert len(entries) == 1
    e = entries[0]
    assert e["outcome"] == "failed"
    assert e["attempts"] == 2
    assert e["assumptions_found"] == 0
    assert e["causal_steps_found"] == 0


def test_log_entry_for_retry_then_success(tmp_path):
    """Malformed attempt 1, well-formed attempt 2. Assert attempts=2, outcome='analyzed'."""
    call_count = {"n": 0}

    def fn(claim_text, feedback):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return {"bad": "response"}
        return {
            "assumptions": [{"text": "OK assumption", "present_in_claim": True}],
            "causal_steps": [{"text": "OK step", "present_in_claim": True}],
        }

    analyze_assumptions(_CLAIM, company_name=_COMPANY, llm_fn=fn, log_dir=str(tmp_path))

    entries = _read_log(tmp_path)
    assert len(entries) == 1
    e = entries[0]
    assert e["attempts"] == 2
    assert e["outcome"] == "analyzed"


def test_bucket_a_and_bucket_d_entries_coexist_in_shared_log(tmp_path):
    """
    One Bucket A call and one Bucket D call with the same log_dir produce
    2 entries in the same file, each correctly tagged with their bucket field.
    """
    from extraction import extract_claim_evidence

    extract_claim_evidence(
        "TSMC accelerated its 100% renewable target to 2040",
        allowlist=["tsmc.com"],
        company_name="TSMC",
        llm_fn=lambda c, f, s: {
            "url": "https://pr.tsmc.com/english/news/3067",
            "quote": (
                "moving its target for 100 percent renewable energy consumption "
                "for all global operations forward to 2040 from 2050"
            ),
        },
        search_fn=lambda q: [
            {
                "url": "https://pr.tsmc.com/english/news/3067",
                "title": "TSMC RE100",
                "snippet": "TSMC moves target to 2040",
            }
        ],
        fetch_fn=lambda url: {
            "success": True,
            "text": (
                "TSMC announced it is moving its target for 100 percent renewable "
                "energy consumption for all global operations forward to 2040 from "
                "2050, accelerating its RE100 commitment by a full decade."
            ),
            "content_type": "text/html",
            "failure_reason": None,
        },
        log_dir=str(tmp_path),
    )

    analyze_assumptions(
        _CLAIM,
        company_name=_COMPANY,
        llm_fn=_make_llm_fn(),
        log_dir=str(tmp_path),
    )

    entries = _read_log(tmp_path)
    assert len(entries) == 2
    buckets = {e["bucket"] for e in entries}
    assert "A" in buckets
    assert "D" in buckets
    a_entries = [e for e in entries if e["bucket"] == "A"]
    d_entries = [e for e in entries if e["bucket"] == "D"]
    assert all(e["company_name"] == "TSMC" for e in a_entries)
    assert all(e["company_name"] == "TSMC" for e in d_entries)


# ---------------------------------------------------------------------------
# Live test (opt-in)
# ---------------------------------------------------------------------------


@pytest.mark.live_api
@pytest.mark.skipif(
    not os.getenv("RUN_LIVE_API"),
    reason="live API test; set RUN_LIVE_API=1 to run deliberately (costs money)",
)
def test_live_analyze_assumptions_tsmc_counterfactual():
    """
    No injected llm_fn. Real OpenAI call on the worked-example claim.
    Both "assumptions_explicit" and "assumptions_not_stated" are possible
    outcomes depending on model behavior, but the worked example has
    explicit content so we assert at least one stated item.
    """
    result = analyze_assumptions(
        _CLAIM,
        company_name=_COMPANY,
    )

    assert isinstance(
        result, AssumptionsStatedEvidence
    ), f"Expected AssumptionsStatedEvidence. Got: {result!r}"
    assert len(result.assumptions) >= 1, (
        f"Expected at least 1 assumption. Full result: assumptions={result.assumptions}, "
        f"causal_steps={result.causal_steps}"
    )
    assert len(result.causal_steps) >= 1, (
        f"Expected at least 1 causal step. Full result: assumptions={result.assumptions}, "
        f"causal_steps={result.causal_steps}"
    )
    assert (
        result.notes == ""
    ), f"notes should be empty on successful LLM call. Got: {result.notes!r}"
    has_stated = any(a.present_in_claim for a in result.assumptions) or any(
        s.present_in_claim for s in result.causal_steps
    )
    assert has_stated, (
        f"Expected at least one present_in_claim=True (the worked example has "
        f"explicit content). Full result: assumptions={result.assumptions}, "
        f"causal_steps={result.causal_steps}"
    )
