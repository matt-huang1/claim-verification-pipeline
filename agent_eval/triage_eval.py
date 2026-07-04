"""Scored spot-check of the triage router against the labeled ground truth.

The adversarial suite (adversarial_eval.py) proves the deterministic verifier
discriminates; this module measures the one LLM judgment in front of it. It
runs every labeled ground-truth claim through triage_claim and scores the
routing against the bucket the claim is filed under in ground_truth.py.

Unlike the adversarial suite this costs real API calls and is
non-deterministic, so it is a deliberately-run measurement
(scripts/triage_eval.py), never a CI gate. With ~a dozen labeled claims the
result is an honest spot-check, not a benchmark — the per-claim reasoning is
part of the output so a human can judge any miss. See
adr/0024-triage-accuracy-eval.md.
"""

from dataclasses import dataclass
from typing import Callable

from agent_eval.bucket_triage import triage_claim
from agent_eval.ground_truth import COMPANY_CLAIMS

# Which ground-truth claim list feeds which expected triage label. Bucket B
# never appears: framework-alignment work is routed by an explicit human
# decision, not by triage (adr/0020-run-pipeline.md).
_CLAIM_LISTS: list[tuple[str, str]] = [
    ("bucket_a_claims", "bucket_a"),
    ("bucket_c_claims", "bucket_c"),
    ("bucket_d_claims", "bucket_d"),
]


@dataclass(frozen=True)
class TriageCase:
    """One labeled claim: the text and the bucket ground truth files it under."""

    case_id: str
    company: str
    claim_text: str
    expected: str  # "bucket_a" | "bucket_c" | "bucket_d"


@dataclass(frozen=True)
class TriageCaseResult:
    """The outcome of routing one TriageCase through triage_claim."""

    case: TriageCase
    actual: str  # triage classification, incl. "ambiguous" / "malformed_llm_response"
    reasoning: str | None

    @property
    def passed(self) -> bool:
        """True if triage routed the claim to its ground-truth bucket."""
        return self.actual == self.case.expected


def build_cases() -> list[TriageCase]:
    """Collect every labeled claim from COMPANY_CLAIMS, in stable order."""
    cases: list[TriageCase] = []
    for company, data in COMPANY_CLAIMS.items():
        for list_name, expected in _CLAIM_LISTS:
            for i, claim in enumerate(data.get(list_name, []), 1):
                cases.append(
                    TriageCase(
                        case_id=f"{company}_{expected}_{i}",
                        company=company,
                        claim_text=claim["claim_text"],
                        expected=expected,
                    )
                )
    return cases


def evaluate_case(
    case: TriageCase, llm_fn: Callable[[str], dict] | None = None
) -> TriageCaseResult:
    """Route one case through triage_claim and record what came back."""
    result = triage_claim(case.claim_text, llm_fn=llm_fn)
    return TriageCaseResult(
        case=case,
        actual=result["classification"],
        reasoning=result["reasoning"],
    )


def run_suite(
    llm_fn: Callable[[str], dict] | None = None,
) -> list[TriageCaseResult]:
    """Evaluate every labeled claim, in order."""
    return [evaluate_case(case, llm_fn=llm_fn) for case in build_cases()]


def summarise(results: list[TriageCaseResult]) -> dict:
    """Aggregate results into headline metrics, with a per-bucket breakdown."""
    by_bucket: dict[str, dict[str, int]] = {}
    for r in results:
        bucket = by_bucket.setdefault(r.case.expected, {"correct": 0, "total": 0})
        bucket["total"] += 1
        if r.passed:
            bucket["correct"] += 1
    correct = sum(1 for r in results if r.passed)
    return {
        "total": len(results),
        "correct": correct,
        "accuracy": correct / len(results) if results else 0.0,
        "by_bucket": by_bucket,
        "all_passed": all(r.passed for r in results),
    }
