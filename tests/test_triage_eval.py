"""
Tests for triage_eval.py — the harness only, never the live model.

The suite construction, scoring, and summary math are deterministic and
tested here with injected fakes. The scored measurement itself
(scripts/triage_eval.py) costs real API calls and is run deliberately.
"""

from agent_eval.ground_truth import COMPANY_CLAIMS
from agent_eval.triage_eval import (
    TriageCase,
    build_cases,
    evaluate_case,
    run_suite,
    summarise,
)

# ---------------------------------------------------------------------------
# build_cases: the suite mirrors the ground truth exactly
# ---------------------------------------------------------------------------


def _ground_truth_count(list_name: str) -> int:
    return sum(len(d.get(list_name, [])) for d in COMPANY_CLAIMS.values())


def test_build_cases_covers_every_labeled_claim():
    cases = build_cases()
    assert len(cases) == (
        _ground_truth_count("bucket_a_claims")
        + _ground_truth_count("bucket_c_claims")
        + _ground_truth_count("bucket_d_claims")
    )


def test_build_cases_expected_labels_match_their_source_lists():
    counts: dict[str, int] = {}
    for case in build_cases():
        counts[case.expected] = counts.get(case.expected, 0) + 1
    assert counts["bucket_a"] == _ground_truth_count("bucket_a_claims")
    assert counts["bucket_c"] == _ground_truth_count("bucket_c_claims")
    assert counts["bucket_d"] == _ground_truth_count("bucket_d_claims")


def test_build_cases_never_expects_bucket_b():
    """Bucket B is routed by explicit human decision, never by triage —
    the eval must not manufacture an expectation triage cannot meet."""
    assert all(c.expected != "bucket_b" for c in build_cases())


def test_case_ids_are_unique():
    ids = [c.case_id for c in build_cases()]
    assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# evaluate_case / run_suite with injected fakes
# ---------------------------------------------------------------------------

_CASE = TriageCase(
    case_id="fake_bucket_a_1",
    company="fake",
    claim_text="FakeCo revenue was 1 billion in 2024",
    expected="bucket_a",
)


def test_correct_routing_passes():
    result = evaluate_case(
        _CASE,
        llm_fn=lambda text: {
            "classification": "bucket_a",
            "reasoning": "single source",
        },
    )
    assert result.passed is True
    assert result.actual == "bucket_a"
    assert result.reasoning == "single source"


def test_wrong_bucket_fails():
    result = evaluate_case(
        _CASE,
        llm_fn=lambda text: {"classification": "bucket_c", "reasoning": "contested"},
    )
    assert result.passed is False


def test_ambiguous_counts_as_a_miss_with_reasoning_kept():
    result = evaluate_case(
        _CASE,
        llm_fn=lambda text: {"classification": "ambiguous", "reasoning": "unclear"},
    )
    assert result.passed is False
    assert result.actual == "ambiguous"
    assert result.reasoning == "unclear"


def test_malformed_llm_response_counts_as_a_miss():
    result = evaluate_case(_CASE, llm_fn=lambda text: {"nonsense": True})
    assert result.passed is False
    assert result.actual == "malformed_llm_response"


def test_run_suite_calls_llm_once_per_labeled_claim():
    calls = {"n": 0}

    def counting_llm(text: str) -> dict:
        calls["n"] += 1
        return {"classification": "bucket_a", "reasoning": "r"}

    results = run_suite(llm_fn=counting_llm)
    assert calls["n"] == len(build_cases())
    assert len(results) == len(build_cases())


# ---------------------------------------------------------------------------
# summarise
# ---------------------------------------------------------------------------


def test_summarise_scores_and_breakdown():
    def routes_everything_to_a(text: str) -> dict:
        return {"classification": "bucket_a", "reasoning": "r"}

    results = run_suite(llm_fn=routes_everything_to_a)
    s = summarise(results)

    a_total = _ground_truth_count("bucket_a_claims")
    assert s["total"] == len(results)
    assert s["correct"] == a_total  # only the real bucket_a claims match
    assert s["by_bucket"]["bucket_a"] == {"correct": a_total, "total": a_total}
    assert s["by_bucket"]["bucket_c"]["correct"] == 0
    assert s["by_bucket"]["bucket_d"]["correct"] == 0
    assert s["all_passed"] is False


def test_summarise_all_correct_is_all_passed():
    def perfect_llm_factory():
        cases = iter(build_cases())

        def llm(text: str) -> dict:
            return {"classification": next(cases).expected, "reasoning": "r"}

        return llm

    results = run_suite(llm_fn=perfect_llm_factory())
    s = summarise(results)
    assert s["correct"] == s["total"]
    assert s["accuracy"] == 1.0
    assert s["all_passed"] is True


def test_summarise_empty_results_is_zero_not_crash():
    s = summarise([])
    assert s["total"] == 0
    assert s["accuracy"] == 0.0
