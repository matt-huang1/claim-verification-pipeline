"""
triage_eval.py (script)

Run the scored triage-accuracy spot-check against the labeled ground truth
and print a per-claim report. Costs real API calls (one per labeled claim,
~a dozen) and is non-deterministic — run deliberately, not in CI:

    python scripts/triage_eval.py

Exits 0 if every claim routed to its ground-truth bucket; exits 1 otherwise.
A miss is information, not necessarily a defect: read the printed reasoning
before concluding anything. See adr/0024-triage-accuracy-eval.md.
"""

import sys
import textwrap
from pathlib import Path

# Scripts live one level below the repo root, so `python scripts/<name>.py`
# from a fresh clone does not put agent_eval/ on sys.path. Adding the root
# makes the scripts runnable without the editable install (dependencies are
# still required — see README).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_eval.triage_eval import run_suite, summarise  # noqa: E402


def main() -> int:
    results = run_suite()

    print("Triage accuracy spot-check — labeled ground-truth claims\n")
    print(f"{'CASE':<28}{'EXPECTED':<12}{'ACTUAL':<24}{'RESULT'}")
    print("-" * 72)
    for r in results:
        mark = "PASS" if r.passed else "FAIL"
        print(f"{r.case.case_id:<28}{r.case.expected:<12}{r.actual:<24}{mark}")
        if not r.passed:
            reason = r.reasoning or "(no reasoning returned)"
            print(
                textwrap.fill(
                    reason,
                    width=68,
                    initial_indent="    > ",
                    subsequent_indent="      ",
                )
            )

    s = summarise(results)
    print("-" * 72)
    print(
        f"\nRouted correctly: {s['correct']}/{s['total']} "
        f"({s['accuracy'] * 100:.0f}%)"
    )
    for bucket, counts in sorted(s["by_bucket"].items()):
        print(f"  {bucket}: {counts['correct']}/{counts['total']}")

    if s["all_passed"]:
        print("\nEvery labeled claim routed to its ground-truth bucket.")
        return 0
    print("\nOne or more claims routed differently — see reasoning above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
