"""
adversarial_eval.py

A deterministic self-evaluation of the Bucket A verifier: does it actually
reject the failures it claims to catch?

WHY THIS EXISTS:

The whole project rests on one thesis — a check only counts if it would have
given a different answer in the world where the claim was false (adr/0001).
Every other module is tested for the world where the claim is TRUE (the
"verified" path). This module tests the mirror world: it deliberately corrupts
a legitimate claim in each way the verifier is supposed to catch, then asserts
the verifier catches it. It turns "we believe the checks discriminate" into a
single reported number — the catch rate — that a reviewer can read at a glance
and a CI job can enforce.

WHY IT IS SELF-CONTAINED AND OFFLINE:

Each case runs through the real, deterministic pipeline (verify_bucket_a_claim
→ domain_check + quote_match), the exact code path a live run uses once a URL
and quote have been proposed. No LLM and no network are involved: the model's
job is only to PROPOSE a url/quote, and this harness supplies adversarial
proposals directly. That is the point — the claim being defended is that the
deterministic verification layer discriminates, independently of whatever the
model says. Documents here are short, clearly-synthetic fixtures written to
exercise one attack each; they are not scraped or confidential content.

THE ATTACKS (each maps to a documented failure mode):

- domain_spoof         — a proposal whose URL is a look-alike/prefix/port-
                         injection domain, not the real one. Must resolve to
                         "source_illegitimate" (domain_check, adr/0002).
- numeric_hallucination — a proposal whose quote changed the single load-
                         bearing number (a year/percentage/figure) to one that
                         does not appear in the document. Must resolve to
                         "numeric_mismatch" (the numeric token gate, adr/0003).
- fabricated_quote     — a proposal whose quote does not appear in the document
                         at all. Must resolve to "no_match".
- clean_control        — an honest proposal. Must resolve to "verified". The
                         controls prove the harness is not trivially passing by
                         rejecting everything.
"""

from dataclasses import dataclass

from agent_eval.pipeline import verify_bucket_a_claim


@dataclass(frozen=True)
class AdversarialCase:
    """One self-evaluation case: an (adversarial or clean) Bucket A proposal
    plus the verifier status it must produce."""

    case_id: str
    attack: str  # "domain_spoof" | "numeric_hallucination"
    #            | "fabricated_quote" | "clean_control"
    description: str
    claim_text: str
    url: str
    allowlist: list[str]
    quote: str
    document: str
    expected_status: str


@dataclass(frozen=True)
class CaseResult:
    """The outcome of running one AdversarialCase through the verifier."""

    case: AdversarialCase
    actual_status: str

    @property
    def passed(self) -> bool:
        """True if the verifier produced the status this case requires.

        For an adversarial case this means the failure was caught with the
        correct, specific status; for a clean control it means the honest
        claim was verified. Either way, a passing result is the verifier
        discriminating exactly as intended.
        """
        return self.actual_status == self.case.expected_status


# --- Fixtures -------------------------------------------------------------
#
# Two short, synthetic disclosure paragraphs. Each contains a real,
# verifiable-looking statement with load-bearing numbers, plus at least one
# OTHER number, so the numeric-token gate is exercised against a document that
# genuinely contains numbers rather than a trivially-empty one.

_TSMC_ALLOWLIST = ["tsmc.com", "pr.tsmc.com"]
_TSMC_URL = "https://pr.tsmc.com/english/news/renewable-target-2040"
_TSMC_DOCUMENT = (
    "TSMC today announced it is moving its target for 100% renewable energy "
    "consumption across all global operations forward to 2040, from the "
    "previous target of 2050. The commitment covers every manufacturing site "
    "worldwide and was approved by the board in September 2023."
)
_TSMC_TRUE_QUOTE = (
    "moving its target for 100% renewable energy consumption across all "
    "global operations forward to 2040"
)
_TSMC_CLAIM = (
    "TSMC is moving its 100% renewable energy target forward to 2040 from 2050"
)

_TOTAL_ALLOWLIST = ["totalenergies.com"]
_TOTAL_URL = "https://totalenergies.com/news/low-carbon-investment-2024"
_TOTAL_DOCUMENT = (
    "TotalEnergies invested close to 5 billion dollars in low-carbon energy "
    "in 2024, and grew net electricity production by 23 percent over the same "
    "year. The company reaffirmed its ambition to reach net zero by 2050."
)
_TOTAL_TRUE_QUOTE = "invested close to 5 billion dollars in low-carbon energy in 2024"
_TOTAL_CLAIM = (
    "TotalEnergies invested close to 5 billion dollars in low-carbon energy " "in 2024"
)


def build_cases() -> list[AdversarialCase]:
    """Construct the fixed self-evaluation suite.

    Every adversarial case is derived from a clean control by corrupting
    exactly one dimension (the URL, a number, or the quote text), so that the
    resulting rejection is attributable to that one change and nothing else.
    """
    return [
        # --- TSMC-derived cases ---
        AdversarialCase(
            case_id="tsmc_clean",
            attack="clean_control",
            description="Honest URL and verbatim quote — must verify.",
            claim_text=_TSMC_CLAIM,
            url=_TSMC_URL,
            allowlist=_TSMC_ALLOWLIST,
            quote=_TSMC_TRUE_QUOTE,
            document=_TSMC_DOCUMENT,
            expected_status="verified",
        ),
        AdversarialCase(
            case_id="tsmc_domain_prefix_spoof",
            attack="domain_spoof",
            description="Real domain used as a prefix of an attacker domain "
            "(tsmc.com.evil.example).",
            claim_text=_TSMC_CLAIM,
            url="https://pr.tsmc.com.evil.example/english/news/renewable-2040",
            allowlist=_TSMC_ALLOWLIST,
            quote=_TSMC_TRUE_QUOTE,
            document=_TSMC_DOCUMENT,
            expected_status="source_illegitimate",
        ),
        AdversarialCase(
            case_id="tsmc_domain_port_injection",
            attack="domain_spoof",
            description="Port/credential injection so netloc ends with the "
            "real domain but the host does not.",
            claim_text=_TSMC_CLAIM,
            url="https://evil.example:.tsmc.com/english/news/renewable-2040",
            allowlist=_TSMC_ALLOWLIST,
            quote=_TSMC_TRUE_QUOTE,
            document=_TSMC_DOCUMENT,
            expected_status="source_illegitimate",
        ),
        AdversarialCase(
            case_id="tsmc_numeric_year_hallucination",
            attack="numeric_hallucination",
            description="Quote changes the target year 2040 -> 2035, a year "
            "absent from the document.",
            claim_text=_TSMC_CLAIM,
            url=_TSMC_URL,
            allowlist=_TSMC_ALLOWLIST,
            quote=(
                "moving its target for 100% renewable energy consumption "
                "across all global operations forward to 2035"
            ),
            document=_TSMC_DOCUMENT,
            expected_status="numeric_mismatch",
        ),
        AdversarialCase(
            case_id="tsmc_fabricated_quote",
            attack="fabricated_quote",
            description="Quote asserts the opposite and appears nowhere in "
            "the document.",
            claim_text=_TSMC_CLAIM,
            url=_TSMC_URL,
            allowlist=_TSMC_ALLOWLIST,
            quote=(
                "TSMC has abandoned its renewable energy commitments and "
                "will not set any further climate targets"
            ),
            document=_TSMC_DOCUMENT,
            expected_status="no_match",
        ),
        # --- TotalEnergies-derived cases ---
        AdversarialCase(
            case_id="totalenergies_clean",
            attack="clean_control",
            description="Honest URL and verbatim quote — must verify.",
            claim_text=_TOTAL_CLAIM,
            url=_TOTAL_URL,
            allowlist=_TOTAL_ALLOWLIST,
            quote=_TOTAL_TRUE_QUOTE,
            document=_TOTAL_DOCUMENT,
            expected_status="verified",
        ),
        AdversarialCase(
            case_id="totalenergies_domain_spoof",
            attack="domain_spoof",
            description="Look-alike domain (totalenergies.com.phish.example).",
            claim_text=_TOTAL_CLAIM,
            url="https://totalenergies.com.phish.example/news/2024",
            allowlist=_TOTAL_ALLOWLIST,
            quote=_TOTAL_TRUE_QUOTE,
            document=_TOTAL_DOCUMENT,
            expected_status="source_illegitimate",
        ),
        AdversarialCase(
            case_id="totalenergies_numeric_hallucination",
            attack="numeric_hallucination",
            description="Quote inflates 5 billion -> 8 billion, a figure "
            "absent from the document.",
            claim_text=_TOTAL_CLAIM,
            url=_TOTAL_URL,
            allowlist=_TOTAL_ALLOWLIST,
            quote=(
                "invested close to 8 billion dollars in low-carbon energy " "in 2024"
            ),
            document=_TOTAL_DOCUMENT,
            expected_status="numeric_mismatch",
        ),
        AdversarialCase(
            case_id="totalenergies_fabricated_quote",
            attack="fabricated_quote",
            description="Quote fabricates a divestment that appears nowhere "
            "in the document.",
            claim_text=_TOTAL_CLAIM,
            url=_TOTAL_URL,
            allowlist=_TOTAL_ALLOWLIST,
            quote=(
                "TotalEnergies has fully exited all oil and gas production "
                "worldwide as of this year"
            ),
            document=_TOTAL_DOCUMENT,
            expected_status="no_match",
        ),
    ]


def evaluate_case(case: AdversarialCase) -> CaseResult:
    """Run one case through the real deterministic verifier and record the
    status it produced."""
    tag = verify_bucket_a_claim(
        claim_id=case.case_id,
        claim_text=case.claim_text,
        url=case.url,
        allowlist=case.allowlist,
        quote=case.quote,
        document=case.document,
    )
    return CaseResult(case=case, actual_status=tag.overall_status)


def run_suite() -> list[CaseResult]:
    """Evaluate every case in the fixed suite, in order."""
    return [evaluate_case(case) for case in build_cases()]


def summarise(results: list[CaseResult]) -> dict:
    """Aggregate results into headline metrics.

    Adversarial and control cases are reported separately: the catch rate is
    the fraction of ADVERSARIAL cases correctly rejected with the right
    status, and it is only meaningful alongside confirmation that every clean
    control still verifies.
    """
    adversarial = [r for r in results if r.case.attack != "clean_control"]
    controls = [r for r in results if r.case.attack == "clean_control"]
    adversarial_caught = sum(1 for r in adversarial if r.passed)
    controls_verified = sum(1 for r in controls if r.passed)
    return {
        "total": len(results),
        "adversarial_total": len(adversarial),
        "adversarial_caught": adversarial_caught,
        "controls_total": len(controls),
        "controls_verified": controls_verified,
        "catch_rate": (adversarial_caught / len(adversarial) if adversarial else 0.0),
        "all_passed": all(r.passed for r in results),
    }
