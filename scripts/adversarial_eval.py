"""
adversarial_eval.py (script)

Run the deterministic adversarial self-evaluation of the Bucket A verifier and
print a human-readable report. Offline and free — no network, no LLM, no API
key:

    python scripts/adversarial_eval.py

Exits 0 if the verifier caught every adversarial case with the correct status
AND verified every clean control; exits 1 otherwise. Suitable as a CI gate.
The same suite is asserted in tests/test_adversarial_eval.py.
"""

import sys

from agent_eval.adversarial_eval import run_suite, summarise


def main() -> int:
    results = run_suite()

    print("Adversarial self-evaluation — Bucket A verifier\n")
    print(f"{'CASE':<38}{'ATTACK':<22}{'RESULT':<8}{'STATUS'}")
    print("-" * 88)
    for r in results:
        mark = "PASS" if r.passed else "FAIL"
        print(f"{r.case.case_id:<38}{r.case.attack:<22}{mark:<8}" f"{r.actual_status}")

    s = summarise(results)
    print("-" * 88)
    print(
        f"\nAdversarial cases caught: "
        f"{s['adversarial_caught']}/{s['adversarial_total']} "
        f"({s['catch_rate'] * 100:.0f}%)"
    )
    print(
        f"Clean controls verified:  " f"{s['controls_verified']}/{s['controls_total']}"
    )

    if s["all_passed"]:
        print("\nAll cases behaved as required.")
        return 0
    print("\nOne or more cases did NOT behave as required — see FAIL rows.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
