"""
Tests for extraction.py.

Most tests here DO NOT call the real LLM. The non-determinism and cost of a
live API call are quarantined into a single test (test_live_tsmc_extraction)
marked `live_api` and skipped unless RUN_LIVE_API is set. Everything else is
deterministic:

- The pre-check gate (is_verifiable_claim) is pure and tested directly.
- The retry-stopping rule (no_meaningful_progress) is pure and tested with
  hand-built AttemptRecords - the "ambiguous 65 then 64 -> stop" /
  "ambiguous 60 then 85 -> continue" cases from the design.
- The full retry loop is tested with a FAKE llm_fn injected in place of the
  real call, against the real TSMC document, so the loop's stopping and
  status behavior is exercised end-to-end with zero API cost.

Why the live test is separated from all the others: it costs real money,
needs network and a valid key, and its output is non-deterministic. Folding
it into the normal suite would make `pytest` flaky, slow, and billable -
the opposite of what a fast deterministic test suite is for. It exists so
the real wiring can be checked deliberately, not on every run.
"""

import os

import pytest

from extraction import (
    AttemptRecord,
    is_verifiable_claim,
    no_meaningful_progress,
    extract_claim_evidence,
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
    llm_fn raises if touched.
    """

    def exploding_llm(claim_text, feedback):
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


# --- full loop with a fake LLM (no API cost) ------------------------------


def test_loop_verifies_on_first_attempt_with_good_proposal(tmp_path):
    def good_llm(claim_text, feedback):
        return {
            "url": "https://pr.tsmc.com/english/news/3067",
            "quote": TSMC_TRUE_QUOTE,
        }

    result = extract_claim_evidence(
        "TSMC accelerated its 100% renewable target to 2040",
        document=TSMC_DOCUMENT,
        allowlist=TSMC_ALLOWLIST,
        llm_fn=good_llm,
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

    def stuck_llm(claim_text, feedback):
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

    def changing_llm(claim_text, feedback):
        seen_feedback.append(feedback)
        return proposals[len(seen_feedback) - 1]

    result = extract_claim_evidence(
        "TSMC moved its renewable target to 2040",
        document=TSMC_DOCUMENT,
        allowlist=TSMC_ALLOWLIST,
        llm_fn=changing_llm,
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
    def stuck_llm(claim_text, feedback):
        return {
            "url": "https://tsmc.com/news",
            "quote": "an unrelated sentence about agricultural yields and farming",
        }

    extract_claim_evidence(
        "TSMC moved its renewable target to 2040",
        document=TSMC_DOCUMENT,
        allowlist=TSMC_ALLOWLIST,
        llm_fn=stuck_llm,
        log_dir=str(tmp_path),
    )
    log_file = tmp_path / "extraction.jsonl"
    assert log_file.exists()
    lines = [line for line in log_file.read_text().splitlines() if line.strip()]
    assert len(lines) == 2  # one per attempt, early-stopped at 2


# --- the single live test (opt-in, costs money) ---------------------------


@pytest.mark.live_api
@pytest.mark.skipif(
    not os.getenv("RUN_LIVE_API"),
    reason="live API test; set RUN_LIVE_API=1 to run deliberately (costs money)",
)
def test_live_tsmc_extraction(tmp_path):
    """
    The one test that calls the real OpenAI API, on the known TSMC claim.
    Opt-in only. Non-deterministic by nature (model output varies), so this
    can occasionally fail even when the code is correct - that is exactly
    why it is isolated from the deterministic suite.
    """
    result = extract_claim_evidence(
        "TSMC accelerated its target for 100 percent renewable energy to 2040",
        document=TSMC_DOCUMENT,
        allowlist=TSMC_ALLOWLIST,
        log_dir=str(tmp_path),
    )
    assert result["status"] == "verified"
