"""
Tests for extraction.py.

Most tests here DO NOT call the real LLM or the real Brave Search API. Both
sources of non-determinism and cost are quarantined into a single test
(test_live_tsmc_extraction) marked `live_api` and skipped unless RUN_LIVE_API
is set. Everything else is deterministic:

- The pre-check gate (is_verifiable_claim) is pure and tested directly.
- The retry-stopping rule (no_meaningful_progress) is pure and tested with
  hand-built AttemptRecords.
- The full retry loop is tested with a FAKE llm_fn AND a FAKE search_fn
  injected in place of the real calls, against the real TSMC document, so
  the loop's stopping and status behavior is exercised end-to-end with zero
  API cost.

All fake llm_fn functions take three arguments: (claim_text, feedback,
search_results). This matches the real signature after search was added to
the loop. Fake search_fn functions return either a fixed list of fake
results (to let the loop proceed to the LLM call) or an empty list (to
exercise the no-search-results path).

Why the live test is separated from all the others: it costs real money,
needs network and valid API keys for both OpenAI and Brave Search, and its
output is non-deterministic. Folding it into the normal suite would make
`pytest` flaky, slow, and billable - the opposite of what a fast
deterministic test suite is for.
"""

import os

import pytest

from extraction import (
    AttemptRecord,
    is_verifiable_claim,
    no_meaningful_progress,
    extract_claim_evidence,
    _NO_SEARCH_RESULTS_FEEDBACK,
)

TSMC_DOCUMENT = """
HSINCHU, Taiwan, R.O.C., Sep. 15, 2023 - To respond to climate change and
mitigate climate impact, TSMC (TWSE: 2330, NYSE: TSM) today announced an
acceleration of its RE100 sustainability timetable, moving its target for
100 percent renewable energy consumption for all global operations
forward to 2040 from 2050. TSMC also raised its 2030 target for
company-wide renewable energy consumption to 60 percent from 40 percent.
"""

TSMC_ALLOWLIST = ["tsmc.com"]
TSMC_TRUE_QUOTE = (
    "moving its target for 100 percent renewable energy consumption "
    "for all global operations forward to 2040 from 2050"
)

# Fake search results used by all loop tests that need search to succeed so the
# LLM call can proceed. The URL just needs to be on the allowlist domain.
_FAKE_SEARCH_RESULTS = [
    {
        "url": "https://pr.tsmc.com/english/news/3067",
        "title": "TSMC Accelerates RE100 Timetable",
        "snippet": "TSMC moves 100% renewable energy target to 2040 from 2050.",
    }
]


def _always_finds(query: str) -> list[dict]:
    return _FAKE_SEARCH_RESULTS


def _record(attempt, status, score):
    return AttemptRecord(
        attempt=attempt,
        url="https://example.com",
        quote="q",
        status=status,
        top_score=score,
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
        document=TSMC_DOCUMENT,
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


# --- full loop with fake LLM and fake search (no API cost) ----------------


def test_loop_verifies_on_first_attempt_with_good_proposal(tmp_path):
    def good_llm(claim_text, feedback, search_results):
        return {
            "url": "https://pr.tsmc.com/english/news/3067",
            "quote": TSMC_TRUE_QUOTE,
        }

    result = extract_claim_evidence(
        "TSMC accelerated its 100% renewable target to 2040",
        document=TSMC_DOCUMENT,
        allowlist=TSMC_ALLOWLIST,
        llm_fn=good_llm,
        search_fn=_always_finds,
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
        document=TSMC_DOCUMENT,
        allowlist=TSMC_ALLOWLIST,
        llm_fn=stuck_llm,
        search_fn=_always_finds,
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
        document=TSMC_DOCUMENT,
        allowlist=TSMC_ALLOWLIST,
        llm_fn=changing_llm,
        search_fn=_always_finds,
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
        document=TSMC_DOCUMENT,
        allowlist=TSMC_ALLOWLIST,
        llm_fn=stuck_llm,
        search_fn=_always_finds,
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
        document=TSMC_DOCUMENT,
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
        document=TSMC_DOCUMENT,
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
        document=TSMC_DOCUMENT,
        allowlist=TSMC_ALLOWLIST,
        llm_fn=capturing_llm,
        search_fn=_always_finds,
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
        document=TSMC_DOCUMENT,
        allowlist=TSMC_ALLOWLIST,
        llm_fn=capturing_llm,
        search_fn=search_succeeds_second_time,
        log_dir=str(tmp_path),
    )
    assert result["status"] == "verified"
    assert result["attempts"] == 2
    # The LLM was called once (on attempt 2, when search succeeded)
    assert len(feedbacks_received) == 1
    # It received the no-search-results feedback from attempt 1
    assert feedbacks_received[0] == _NO_SEARCH_RESULTS_FEEDBACK


# --- the single live test (opt-in, costs money) ---------------------------


@pytest.mark.live_api
@pytest.mark.skipif(
    not os.getenv("RUN_LIVE_API"),
    reason="live API test; set RUN_LIVE_API=1 to run deliberately (costs money)",
)
def test_live_tsmc_extraction(tmp_path):
    """
    Calls both the real Brave Search API and the real OpenAI API.
    Opt-in only (RUN_LIVE_API=1). Requires both OPENAI_API_KEY and
    BRAVE_API_KEY to be set in the environment.

    Non-deterministic: search results and model output both vary. Can
    occasionally fail even when the code is correct — that is exactly why
    this is isolated from the deterministic suite. Note that scope has
    grown since the original version: this now exercises the full live
    pipeline including real web search, not just a bare LLM call against
    hardcoded document text.
    """
    result = extract_claim_evidence(
        "TSMC accelerated its target for 100 percent renewable energy to 2040",
        document=TSMC_DOCUMENT,
        allowlist=TSMC_ALLOWLIST,
        log_dir=str(tmp_path),
    )
    assert result["status"] == "verified"
