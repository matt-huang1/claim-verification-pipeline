"""
Tests for extraction.py.

All tests that exercise the retry loop inject fake llm_fn, search_fn, and
fetch_fn so no real API or HTTP calls are made. The pre-check tests and
stopping-rule tests are pure-Python with no injection needed.

Test organisation:
  - pre-check gate (is_verifiable_claim)
  - stopping rule (no_meaningful_progress, pure)
  - fetch feedback (direct tests of _build_fetch_feedback)
  - full loop with fake LLM, fake search, fake fetch (no API cost)
  - search integration (search empty → fail immediately, no LLM)
  - URL-in-results enforcement (deterministic check via same_url)
  - fetch integration (fetch fail → skip verification; mixed sequences)
  - live API (opt-in, costs money)
"""

import json
import os

import pytest
from unittest.mock import MagicMock, patch

from extraction import (
    AttemptRecord,
    _NO_SEARCH_RESULTS_FEEDBACK,
    _build_fetch_feedback,
    extract_claim_evidence,
    is_verifiable_claim,
    no_meaningful_progress,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

TSMC_DOCUMENT = (
    "TSMC announced it is moving its target for 100 percent renewable energy "
    "consumption for all global operations forward to 2040 from 2050, "
    "accelerating its RE100 commitment by a full decade."
)

TSMC_ALLOWLIST = ["tsmc.com", "pr.tsmc.com"]

TSMC_TRUE_QUOTE = (
    "moving its target for 100 percent renewable energy consumption for all "
    "global operations forward to 2040 from 2050"
)

_FAKE_SEARCH_RESULTS = [
    {
        "url": "https://pr.tsmc.com/english/news/3067",
        "title": "TSMC Accelerates RE100 Timetable",
        "snippet": "TSMC moves 100% renewable energy target to 2040 from 2050.",
    },
    {
        "url": "https://tsmc.com/news",
        "title": "TSMC News",
        "snippet": "Latest news from TSMC.",
    },
]


def _always_finds(query):
    return _FAKE_SEARCH_RESULTS


def _fake_fetch(url: str) -> dict:
    return {
        "success": True,
        "text": TSMC_DOCUMENT,
        "content_type": "text/html",
        "failure_reason": None,
    }


def _record(attempt, status, score, stage_reached="verification_completed"):
    return AttemptRecord(
        attempt=attempt,
        url="https://example.com",
        quote="q",
        status=status,
        top_score=score,
        stage_reached=stage_reached,
        timestamp="2026-01-01T00:00:00+00:00",
    )


# --- pre-check gate -------------------------------------------------------


def test_gate_passes_claim_with_a_number():
    assert is_verifiable_claim("TSMC moved its renewable target to 2040") is True


def test_gate_passes_claim_with_percentage():
    assert is_verifiable_claim("renewable energy reached 60 percent") is True


def test_gate_passes_claim_with_exclusivity_word_first():
    assert is_verifiable_claim("TSMC was the first foundry to do this") is True


def test_gate_passes_claim_with_exclusivity_word_worlds_largest():
    assert is_verifiable_claim("TSMC is the world's largest pure-play foundry") is True


def test_gate_rejects_vague_claim_with_no_number_or_ranking_word():
    assert (
        is_verifiable_claim("TSMC makes good chips and cares about sustainability")
        is False
    )


def test_vague_claim_is_rejected_without_calling_the_llm(tmp_path):
    """
    A claim that fails the pre-check must return too_vague_to_verify and
    must NOT call the LLM at all (the cost-control guarantee). The injected
    llm_fn raises if touched. No search_fn needed: the loop is never entered.
    """

    def exploding_llm(claim_text, feedback, search_results):
        raise AssertionError("LLM must not be called for a too-vague claim")

    result = extract_claim_evidence(
        "TSMC cares deeply about the environment",
        allowlist=TSMC_ALLOWLIST,
        llm_fn=exploding_llm,
        log_dir=str(tmp_path),
    )
    assert result["status"] == "too_vague_to_verify"
    assert result["attempts"] == 0
    assert result["last_attempt_status"] is None


# --- stopping rule (pure) -------------------------------------------------


def test_no_progress_same_status_score_drops_stops():
    """ambiguous 65 then ambiguous 64 -> no meaningful progress -> stop."""
    prev = _record(1, "ambiguous", 65.0)
    curr = _record(2, "ambiguous", 64.0)
    assert no_meaningful_progress(prev, curr) is True


def test_progress_same_status_score_jumps_continues():
    """ambiguous 60 then ambiguous 85 -> real improvement -> continue."""
    prev = _record(1, "ambiguous", 60.0)
    curr = _record(2, "ambiguous", 85.0)
    assert no_meaningful_progress(prev, curr) is False


def test_different_status_always_counts_as_progress():
    prev = _record(1, "no_match", 40.0)
    curr = _record(2, "numeric_mismatch", 41.0)
    assert no_meaningful_progress(prev, curr) is False


def test_none_scores_same_status_count_as_no_progress():
    prev = _record(1, "quote_too_short", None)
    curr = _record(2, "quote_too_short", None)
    assert no_meaningful_progress(prev, curr) is True


def test_different_stage_reached_always_counts_as_progress():
    """fetch_failed then verification_completed = progress regardless of status."""
    prev = _record(1, "not_found", None, stage_reached="fetch_failed")
    curr = _record(2, "ambiguous", None, stage_reached="verification_completed")
    assert no_meaningful_progress(prev, curr) is False


def test_same_stage_same_status_none_score_is_no_progress():
    """Two fetch_failed / not_found in a row = no progress."""
    prev = _record(1, "not_found", None, stage_reached="fetch_failed")
    curr = _record(2, "not_found", None, stage_reached="fetch_failed")
    assert no_meaningful_progress(prev, curr) is True


# --- fetch feedback (direct) ----------------------------------------------


def test_build_fetch_feedback_not_found():
    msg = _build_fetch_feedback("not_found")
    assert "404" in msg or "Not Found" in msg


def test_build_fetch_feedback_forbidden():
    msg = _build_fetch_feedback("forbidden")
    assert "403" in msg or "authentication" in msg or "Forbidden" in msg


def test_build_fetch_feedback_timeout():
    msg = _build_fetch_feedback("timeout")
    assert "timed out" in msg


def test_build_fetch_feedback_unknown_reason_returns_catch_all():
    msg = _build_fetch_feedback("some_future_reason")
    assert "could not be fetched" in msg


# --- full loop with fake LLM and fake search (no API cost) ----------------


def test_loop_verifies_on_first_attempt_with_good_proposal(tmp_path):
    def good_llm(claim_text, feedback, search_results):
        return {
            "url": "https://pr.tsmc.com/english/news/3067",
            "quote": TSMC_TRUE_QUOTE,
        }

    result = extract_claim_evidence(
        "TSMC accelerated its 100% renewable target to 2040",
        allowlist=TSMC_ALLOWLIST,
        llm_fn=good_llm,
        search_fn=_always_finds,
        fetch_fn=_fake_fetch,
        log_dir=str(tmp_path),
    )
    assert result["status"] == "verified"
    assert result["attempts"] == 1
    assert result["last_attempt_status"] == "verified"


def test_loop_early_stops_on_two_no_progress_attempts(tmp_path):
    """
    The LLM keeps proposing the same out-of-document quote (legit domain,
    but quote not present): no_match at the same score twice -> early stop
    at attempt 2, never reaching the hard cap of 3.
    """
    calls = []

    def stuck_llm(claim_text, feedback, search_results):
        calls.append(feedback)
        return {
            "url": "https://tsmc.com/news",
            "quote": "an unrelated sentence about agricultural yields and farming",
        }

    result = extract_claim_evidence(
        "TSMC moved its renewable target to 2040",
        allowlist=TSMC_ALLOWLIST,
        llm_fn=stuck_llm,
        search_fn=_always_finds,
        fetch_fn=_fake_fetch,
        log_dir=str(tmp_path),
    )
    assert result["status"] == "unverifiable_after_retries"
    assert result["attempts"] == 2  # stopped early, did not use all 3
    assert result["last_attempt_status"] == "no_match"


def test_loop_runs_to_hard_cap_when_statuses_keep_changing(tmp_path):
    """
    Three attempts, each a different failure status, so the no-progress
    early stop never triggers (different status = progress) and the loop
    runs to the hard cap of 3, then returns unverifiable_after_retries.
    Also confirms feedback is threaded into later calls.
    """
    proposals = [
        # attempt 1: too short -> quote_too_short
        {"url": "https://tsmc.com/news", "quote": "by 2040"},
        # attempt 2: long but absent -> no_match
        {
            "url": "https://tsmc.com/news",
            "quote": "an unrelated sentence about agricultural yields and farming",
        },
        # attempt 3: high text match but wrong year 2035 -> numeric_mismatch
        {
            "url": "https://tsmc.com/news",
            "quote": (
                "moving its target for 100 percent renewable energy "
                "consumption for all global operations forward to 2035 "
                "from 2050"
            ),
        },
    ]
    seen_feedback = []

    def changing_llm(claim_text, feedback, search_results):
        seen_feedback.append(feedback)
        return proposals[len(seen_feedback) - 1]

    result = extract_claim_evidence(
        "TSMC moved its renewable target to 2040",
        allowlist=TSMC_ALLOWLIST,
        llm_fn=changing_llm,
        search_fn=_always_finds,
        fetch_fn=_fake_fetch,
        log_dir=str(tmp_path),
    )
    assert result["status"] == "unverifiable_after_retries"
    assert result["attempts"] == 3
    assert result["last_attempt_status"] == "numeric_mismatch"
    # first call has no feedback; later calls do
    assert seen_feedback[0] is None
    assert seen_feedback[1] is not None
    assert seen_feedback[2] is not None


def test_loop_writes_one_log_line_per_attempt(tmp_path):
    def stuck_llm(claim_text, feedback, search_results):
        return {
            "url": "https://tsmc.com/news",
            "quote": "an unrelated sentence about agricultural yields and farming",
        }

    extract_claim_evidence(
        "TSMC moved its renewable target to 2040",
        allowlist=TSMC_ALLOWLIST,
        llm_fn=stuck_llm,
        search_fn=_always_finds,
        fetch_fn=_fake_fetch,
        log_dir=str(tmp_path),
    )
    log_file = tmp_path / "extraction.jsonl"
    assert log_file.exists()
    lines = [line for line in log_file.read_text().splitlines() if line.strip()]
    assert len(lines) == 2  # one per attempt, early-stopped at 2


# --- search integration ---------------------------------------------------


def test_loop_skips_llm_when_search_returns_empty(tmp_path):
    """
    When search returns no results, the LLM must NOT be called for that
    attempt — the attempt fails immediately as "no_search_results". This is a
    firm rule: the loop never falls back to asking the model for a URL from
    memory. The no-search failure counts toward the hard cap AND the
    no-progress early stop (two identical no_search_results → stop at
    attempt 2, not 3).
    """

    def exploding_llm(claim_text, feedback, search_results):
        raise AssertionError(
            "LLM must not be called when search returns empty — "
            "no memory-fallback allowed"
        )

    result = extract_claim_evidence(
        "TSMC moved its renewable target to 2040",
        allowlist=TSMC_ALLOWLIST,
        llm_fn=exploding_llm,
        search_fn=lambda q: [],
        log_dir=str(tmp_path),
    )
    assert result["status"] == "unverifiable_after_retries"
    # Two identical no_search_results → no-progress early stop fires at attempt 2
    assert result["attempts"] == 2
    assert result["last_attempt_status"] == "no_search_results"


def test_loop_logs_no_search_results_attempts(tmp_path):
    """
    no_search_results attempts are logged just like verification-failure
    attempts — every attempt to the log, regardless of how it failed.
    """
    extract_claim_evidence(
        "TSMC moved its renewable target to 2040",
        allowlist=TSMC_ALLOWLIST,
        llm_fn=lambda c, f, s: (_ for _ in ()).throw(
            AssertionError("must not reach LLM")
        ),
        search_fn=lambda q: [],
        log_dir=str(tmp_path),
    )
    log_file = tmp_path / "extraction.jsonl"
    lines = [line for line in log_file.read_text().splitlines() if line.strip()]
    assert len(lines) == 2  # two no_search_results attempts before early stop


def test_loop_passes_search_results_to_llm(tmp_path):
    """
    When search returns candidates, they are passed as the third argument to
    llm_fn. The model selects from real candidates, not from memory.
    """
    received_search = []

    def capturing_llm(claim_text, feedback, search_results):
        received_search.append(search_results)
        return {
            "url": _FAKE_SEARCH_RESULTS[0]["url"],
            "quote": TSMC_TRUE_QUOTE,
        }

    result = extract_claim_evidence(
        "TSMC accelerated its 100% renewable target to 2040",
        allowlist=TSMC_ALLOWLIST,
        llm_fn=capturing_llm,
        search_fn=_always_finds,
        fetch_fn=_fake_fetch,
        log_dir=str(tmp_path),
    )
    assert result["status"] == "verified"
    assert len(received_search) == 1
    assert received_search[0] == _FAKE_SEARCH_RESULTS


def test_no_search_results_feedback_is_set_for_next_attempt(tmp_path):
    """
    After a no_search_results attempt, if the next attempt finds results,
    the LLM receives the no-search feedback message from the previous
    attempt — so the model is informed that an earlier attempt found nothing,
    rather than getting a blank first call.
    """
    feedbacks_received = []
    attempt_count = [0]

    def search_succeeds_second_time(query):
        attempt_count[0] += 1
        if attempt_count[0] == 1:
            return []
        return _FAKE_SEARCH_RESULTS

    def capturing_llm(claim_text, feedback, search_results):
        feedbacks_received.append(feedback)
        return {
            "url": _FAKE_SEARCH_RESULTS[0]["url"],
            "quote": TSMC_TRUE_QUOTE,
        }

    result = extract_claim_evidence(
        "TSMC accelerated its 100% renewable target to 2040",
        allowlist=TSMC_ALLOWLIST,
        llm_fn=capturing_llm,
        search_fn=search_succeeds_second_time,
        fetch_fn=_fake_fetch,
        log_dir=str(tmp_path),
    )
    assert result["status"] == "verified"
    assert result["attempts"] == 2
    # The LLM was called once (on attempt 2, when search succeeded)
    assert len(feedbacks_received) == 1
    # It received the no-search-results feedback from attempt 1
    assert feedbacks_received[0] == _NO_SEARCH_RESULTS_FEEDBACK


# --- URL-in-results enforcement -------------------------------------------


def test_loop_rejects_url_not_from_search_results(tmp_path):
    """
    A URL that is on the allowlist but was NOT in the search results provided
    to that attempt must fail immediately as "url_not_from_search_results",
    and verify_bucket_a_claim must never be called. This is the deterministic
    enforcement of the "select from candidates" rule that the prompt instruction
    alone cannot provide.

    The two consecutive url_not_from_search_results attempts (both score=None)
    trigger the no-progress early stop at attempt 2, not the hard cap of 3.
    """

    def offlist_llm(claim_text, feedback, search_results):
        # tsmc.com is on the allowlist, but this path is not in _FAKE_SEARCH_RESULTS
        return {"url": "https://tsmc.com/invented-page", "quote": TSMC_TRUE_QUOTE}

    with patch("extraction.verify_bucket_a_claim") as mock_verify:
        result = extract_claim_evidence(
            "TSMC moved its renewable target to 2040",
            allowlist=TSMC_ALLOWLIST,
            llm_fn=offlist_llm,
            search_fn=_always_finds,
            log_dir=str(tmp_path),
        )

    mock_verify.assert_not_called()
    assert result["status"] == "unverifiable_after_retries"
    assert result["attempts"] == 2
    assert result["last_attempt_status"] == "url_not_from_search_results"


def test_loop_accepts_url_with_trivial_formatting_difference(tmp_path):
    """
    A URL differing from a search result only by a trailing slash is treated
    as matching — the formatting difference is normalized away by same_url().
    The attempt proceeds to verification normally (not rejected as
    url_not_from_search_results).
    """

    def trailing_slash_llm(claim_text, feedback, search_results):
        # Add trailing slash to the first search result URL
        return {
            "url": _FAKE_SEARCH_RESULTS[0]["url"] + "/",
            "quote": TSMC_TRUE_QUOTE,
        }

    result = extract_claim_evidence(
        "TSMC accelerated its 100% renewable target to 2040",
        allowlist=TSMC_ALLOWLIST,
        llm_fn=trailing_slash_llm,
        search_fn=_always_finds,
        fetch_fn=_fake_fetch,
        log_dir=str(tmp_path),
    )
    # Normalized URL matched → proceeded to verification → verified
    assert result["status"] == "verified"
    assert result["last_attempt_status"] != "url_not_from_search_results"


# --- fetch integration ----------------------------------------------------


def test_loop_stops_early_on_repeated_fetch_failure(tmp_path):
    """
    A fetch_fn that always returns failure causes the loop to stop early:
    two consecutive fetch_failed / not_found attempts = no progress.
    verify_bucket_a_claim is never reached.
    """

    def always_fails_fetch(url):
        return {
            "success": False,
            "text": None,
            "content_type": None,
            "failure_reason": "not_found",
        }

    with patch("extraction.verify_bucket_a_claim") as mock_verify:
        result = extract_claim_evidence(
            "TSMC moved its renewable target to 2040",
            allowlist=TSMC_ALLOWLIST,
            llm_fn=lambda c, f, s: {
                "url": _FAKE_SEARCH_RESULTS[0]["url"],
                "quote": TSMC_TRUE_QUOTE,
            },
            search_fn=_always_finds,
            fetch_fn=always_fails_fetch,
            log_dir=str(tmp_path),
        )

    mock_verify.assert_not_called()
    assert result["status"] == "unverifiable_after_retries"
    assert result["attempts"] == 2  # early stop, not hard cap of 3
    assert result["last_attempt_status"] == "not_found"


def test_loop_fetch_fail_then_verification_counts_as_progress(tmp_path):
    """
    Attempt 1 fails at fetch (stage_reached='fetch_failed').
    Attempt 2 fetch succeeds but verification fails (stage_reached='verification_completed').
    Different stage_reached = progress, so the early-stop rule does NOT fire
    after attempt 2. The loop exhausts max_attempts=2 normally.
    The log shows the correct stage_reached for each attempt.
    """
    fetch_calls = [0]

    def once_failing_fetch(url):
        fetch_calls[0] += 1
        if fetch_calls[0] == 1:
            return {
                "success": False,
                "text": None,
                "content_type": None,
                "failure_reason": "not_found",
            }
        return {
            "success": True,
            "text": TSMC_DOCUMENT,
            "content_type": "text/html",
            "failure_reason": None,
        }

    mock_tag = MagicMock()
    mock_tag.overall_status = "ambiguous"
    mock_tag.quote_evidence = None

    with patch("extraction.verify_bucket_a_claim", return_value=mock_tag):
        result = extract_claim_evidence(
            "TSMC moved its renewable target to 2040",
            allowlist=TSMC_ALLOWLIST,
            llm_fn=lambda c, f, s: {
                "url": _FAKE_SEARCH_RESULTS[0]["url"],
                "quote": TSMC_TRUE_QUOTE,
            },
            search_fn=_always_finds,
            fetch_fn=once_failing_fetch,
            max_attempts=2,
            log_dir=str(tmp_path),
        )

    # Different stages → no early stop → loop ran to max_attempts=2
    assert result["status"] == "unverifiable_after_retries"
    assert result["attempts"] == 2
    assert result["last_attempt_status"] == "ambiguous"

    # Verify stage_reached in log entries
    log_file = tmp_path / "extraction.jsonl"
    entries = [
        json.loads(line) for line in log_file.read_text().splitlines() if line.strip()
    ]
    assert entries[0]["stage_reached"] == "fetch_failed"
    assert entries[1]["stage_reached"] == "verification_completed"


# --- the single live test (opt-in, costs money) ---------------------------


@pytest.mark.live_api
@pytest.mark.skipif(
    not os.getenv("RUN_LIVE_API"),
    reason="live API test; set RUN_LIVE_API=1 to run deliberately (costs money)",
)
def test_live_tsmc_extraction(tmp_path):
    """
    Exercises the full real pipeline: real Tavily Search API call, real OpenAI
    API call, and a real HTTP fetch of whatever URL the model selects from
    the search results. All three external calls cost money and are subject
    to rate limits and availability. This test is non-deterministic: search
    results, model output, and fetched page content all vary across runs. It
    can occasionally fail even when the code is correct — that is exactly why
    it is isolated from the deterministic suite with RUN_LIVE_API=1 opt-in.
    Requires OPENAI_API_KEY and TAVILY_API_KEY in the environment.
    """
    result = extract_claim_evidence(
        "TSMC accelerated its target for 100 percent renewable energy to 2040",
        allowlist=TSMC_ALLOWLIST,
        log_dir=str(tmp_path),
    )
    assert result["status"] == "verified"
