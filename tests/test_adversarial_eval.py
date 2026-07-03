"""
Tests for the adversarial self-evaluation harness.

These assert the headline property the harness exists to demonstrate: the
deterministic Bucket A verifier catches every adversarial proposal with the
correct, specific status, and still verifies every honest one. If a future
change silently weakened a check, one of these would go red.
"""

from agent_eval.adversarial_eval import (
    AdversarialCase,
    build_cases,
    evaluate_case,
    run_suite,
    summarise,
)

_EXPECTED_BY_ATTACK = {
    "domain_spoof": "source_illegitimate",
    "numeric_hallucination": "numeric_mismatch",
    "fabricated_quote": "no_match",
    "clean_control": "verified",
}


def test_suite_is_non_trivial():
    """The suite must contain both adversarial cases and clean controls, so a
    100% catch rate cannot be achieved by rejecting everything."""
    cases = build_cases()
    attacks = {c.attack for c in cases}
    assert "clean_control" in attacks
    assert attacks - {"clean_control"}, "suite has no adversarial cases"


def test_every_case_maps_to_the_documented_status():
    """Each case's expected_status matches the status its attack type should
    produce — the fixtures cannot drift from the documented contract."""
    for case in build_cases():
        assert case.expected_status == _EXPECTED_BY_ATTACK[case.attack]


def test_every_adversarial_case_is_caught():
    for result in run_suite():
        assert result.passed, (
            f"{result.case.case_id} ({result.case.attack}): expected "
            f"{result.case.expected_status}, got {result.actual_status}"
        )


def test_catch_rate_is_total_and_controls_verify():
    summary = summarise(run_suite())
    assert summary["catch_rate"] == 1.0
    assert summary["controls_verified"] == summary["controls_total"]
    assert summary["all_passed"] is True


def test_clean_controls_verify_and_would_fail_if_corrupted():
    """A control verifies; the same claim with a spoofed domain does not.
    This is the discrimination property stated directly: the check gives a
    different answer in the world where the source is illegitimate."""
    clean = next(c for c in build_cases() if c.attack == "clean_control")
    assert evaluate_case(clean).actual_status == "verified"

    spoofed = AdversarialCase(
        case_id=clean.case_id + "_spoofed",
        attack="domain_spoof",
        description="control with its domain swapped for an attacker domain",
        claim_text=clean.claim_text,
        url="https://" + clean.allowlist[0] + ".attacker.example/x",
        allowlist=clean.allowlist,
        quote=clean.quote,
        document=clean.document,
        expected_status="source_illegitimate",
    )
    assert evaluate_case(spoofed).actual_status == "source_illegitimate"
