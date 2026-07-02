"""
Unit tests for src/review.py — pure presentation, no API or pipeline calls.
"""

from src.tag_schema import (
    AssumptionItem,
    AssumptionsStatedEvidence,
    CausalStep,
    ClaimTag,
    CriterionEvidence,
    DefinitionGroup,
    DistinctFinding,
    DomainCheckEvidence,
    QuoteMatchEvidence,
    SourcePluralityEvidence,
    TPIManagementQualityEvidence,
    UnresolvedFinding,
)
from src.review import format_tag, format_result

# ─── helpers ──────────────────────────────────────────────────────────────────


def _tag_a(domain_passed=True, quote_status="unique", matched_text="exact text"):
    return ClaimTag(
        claim_id="test-a",
        claim_text="TSMC committed to 100% renewable electricity by 2040.",
        bucket="A",
        timestamp="2026-07-02T00:00:00+00:00",
        domain_evidence=DomainCheckEvidence(
            domain="tsmc.com",
            passed=domain_passed,
            matched_entry="tsmc.com" if domain_passed else None,
        ),
        quote_evidence=QuoteMatchEvidence(
            status=quote_status,
            top_score=0.95 if quote_status == "unique" else 0.2,
            matched_text=matched_text,
            candidate_count=1,
        ),
    )


# ─── Bucket A ─────────────────────────────────────────────────────────────────


def test_format_tag_bucket_a_verified():
    out = format_tag(_tag_a())
    assert "verified" in out
    assert "✓" in out
    assert "TSMC committed to 100% renewable electricity by 2040." in out


def test_format_tag_bucket_a_source_illegitimate():
    out = format_tag(_tag_a(domain_passed=False))
    assert "source_illegitimate" in out
    assert "✗" in out


# ─── Bucket B — NZIF criteria ─────────────────────────────────────────────────


def test_format_tag_bucket_b_criteria_evidence():
    tag = ClaimTag(
        claim_id="test-b",
        claim_text="Company X has a credible decarbonisation plan.",
        bucket="B",
        timestamp="2026-07-02T00:00:00+00:00",
        criteria_evidence=[
            CriterionEvidence(
                criterion_name="decarbonisation_plan",
                criterion_text="The company must have a documented plan to reach net zero.",
                evidence_text="Our roadmap targets net-zero emissions by 2050.",
                evidence_source_url="https://example.com/report",
                evidence_source_type="official",
            ),
            CriterionEvidence(
                criterion_name="disclosure",
                criterion_text="The company must disclose Scope 1, 2, and 3 emissions.",
                evidence_text="We report Scope 1, 2, and 3 emissions annually.",
                evidence_source_url="https://example.com/esg",
                evidence_source_type="third_party",
            ),
        ],
    )
    out = format_tag(tag)
    assert "decarbonisation_plan" in out
    assert "disclosure" in out
    assert "https://example.com/report" in out
    assert "https://example.com/esg" in out
    assert "human review required" in out


# ─── Bucket B — TPI ───────────────────────────────────────────────────────────


def test_format_tag_bucket_b_tpi_evidence():
    indicators = {i: "yes" for i in range(1, 21)}
    indicators[21] = "no"
    indicators[22] = "no"
    indicators[23] = "yes"
    tag = ClaimTag(
        claim_id="test-b-tpi",
        claim_text="TotalEnergies has Level 5 TPI management quality.",
        bucket="B",
        timestamp="2026-07-02T00:00:00+00:00",
        tpi_evidence=TPIManagementQualityEvidence(
            company_tpi_id="1216",
            company_slug="totalenergies",
            overall_level=5,
            current_level_date="2024-01-15",
            indicator_results=indicators,
            historical_levels=[("2022-01-01", 4), ("2024-01-15", 5)],
            max_level=5,
        ),
    )
    out = format_tag(tag)
    assert "Level 5" in out or "5" in out
    assert "Failing" in out
    assert "21" in out
    assert "22" in out


def test_format_tag_bucket_b_tpi_not_applicable_shown_separately():
    """
    A "not-applicable" indicator must appear under its own
    "Not applicable" section and must NOT appear under "Failing".

    Confirmed against Antofagasta's real TPI result: indicator 12
    is "not-applicable" at Level 3 (not assessed at this level),
    indicators 10 and 21 are "no" (genuinely failing).
    """
    indicators = {i: "yes" for i in range(1, 24)}
    indicators[10] = "no"
    indicators[12] = "not-applicable"
    indicators[21] = "no"
    tag = ClaimTag(
        claim_id="test-b-tpi-na",
        claim_text="Antofagasta TPI Management Quality.",
        bucket="B",
        timestamp="2026-07-02T00:00:00+00:00",
        tpi_evidence=TPIManagementQualityEvidence(
            company_tpi_id="2739",
            company_slug="antofagasta",
            overall_level=3,
            current_level_date="2024-12-15",
            indicator_results=indicators,
            historical_levels=[("2024-12-15", 3)],
            max_level=5,
        ),
    )
    out = format_tag(tag)
    assert "Not applicable" in out
    assert "12" in out
    # indicator 12 must NOT appear in the Failing line
    lines = out.splitlines()
    failing_line = next((ln for ln in lines if ln.strip().startswith("Failing")), "")
    assert (
        "12" not in failing_line
    ), f"Indicator 12 should not appear in Failing line: {failing_line!r}"
    # indicators 10 and 21 must appear in the Failing line
    assert "10" in failing_line
    assert "21" in failing_line


# ─── Bucket C ─────────────────────────────────────────────────────────────────


def test_format_tag_bucket_c_disambiguated():
    spe = SourcePluralityEvidence(
        sources_checked=4,
        groups=[
            DefinitionGroup(
                member_source_urls=[
                    "https://source1.com",
                    "https://source2.com",
                ],
                shared_definition_label="Global smartphone market",
                reasoning="Both define market as worldwide smartphone shipments.",
            )
        ],
        distinct_sources=[
            DistinctFinding(
                source_url="https://source3.com",
                reasoning="Defines market as North America only.",
            )
        ],
        unresolved=[
            UnresolvedFinding(
                source_url="https://source4.com",
                reasoning="Definition text too vague to place.",
            )
        ],
        no_definition_sources=[],
        failed_reconciliation=[],
        notes="",
    )
    tag = ClaimTag(
        claim_id="test-c",
        claim_text="Apple holds 60% of the smartphone market.",
        bucket="C",
        timestamp="2026-07-02T00:00:00+00:00",
        source_plurality_evidence=spe,
    )
    out = format_tag(tag)
    assert "Global smartphone market" in out
    assert "Distinct sources" in out
    assert "Unresolved" in out
    # Zero-count sections must not appear
    assert "No stated definition" not in out
    assert "Failed reconciliation" not in out


def test_format_tag_bucket_c_empty_sections_omitted():
    spe = SourcePluralityEvidence(
        sources_checked=2,
        groups=[
            DefinitionGroup(
                member_source_urls=["https://a.com", "https://b.com"],
                shared_definition_label="Revenue market share",
                reasoning="Same methodology.",
            )
        ],
        distinct_sources=[],
        unresolved=[],
        no_definition_sources=[],
        failed_reconciliation=[],
        notes="",
    )
    tag = ClaimTag(
        claim_id="test-c2",
        claim_text="Company Y holds 40% revenue share.",
        bucket="C",
        timestamp="2026-07-02T00:00:00+00:00",
        source_plurality_evidence=spe,
    )
    out = format_tag(tag)
    assert "Distinct sources" not in out
    assert "Unresolved" not in out


# ─── Bucket D ─────────────────────────────────────────────────────────────────


def test_format_tag_bucket_d_explicit():
    ae = AssumptionsStatedEvidence(
        assumptions=[
            AssumptionItem(
                text="TSMC is the sole supplier of advanced chips.",
                present_in_claim=True,
            ),
            AssumptionItem(
                text="No alternative supplier could ramp up within a year.",
                present_in_claim=False,
            ),
        ],
        causal_steps=[
            CausalStep(
                text="TSMC absence → chip shortage",
                present_in_claim=True,
            ),
            CausalStep(
                text="Chip shortage → GDP contraction",
                present_in_claim=False,
            ),
        ],
        notes="",
    )
    tag = ClaimTag(
        claim_id="test-d",
        claim_text="If TSMC halted production, GDP would fall by 3%.",
        bucket="D",
        timestamp="2026-07-02T00:00:00+00:00",
        assumptions_evidence=ae,
    )
    out = format_tag(tag)
    assert "✓" in out
    assert "✗" in out
    assert "present in claim" in out
    assert "NOT stated in claim" in out


# ─── format_result ────────────────────────────────────────────────────────────


def test_format_result_with_tag():
    tag = _tag_a()
    result = {
        "outcome": "completed",
        "bucket": "C",
        "triage_reasoning": "contested definition",
        "tag": tag,
    }
    out = format_result(result)
    assert "PIPELINE RESULT" in out
    assert "contested definition" in out
    # tag content appears
    assert "TSMC committed to 100% renewable electricity" in out


def test_format_result_without_tag():
    result = {
        "outcome": "triage_failed",
        "bucket": None,
        "triage_reasoning": None,
        "tag": None,
    }
    out = format_result(result)
    assert "No tag produced" in out
    assert "triage_failed" in out


def test_format_result_ambiguous():
    result = {
        "outcome": "ambiguous",
        "bucket": "C",
        "triage_reasoning": "cannot classify",
        "tag": None,
    }
    out = format_result(result)
    assert "ambiguous" in out
    assert "cannot classify" in out


# ─── wrapping & safety ────────────────────────────────────────────────────────


def test_long_text_is_wrapped_not_truncated():
    long_evidence = "A" * 150 + " " + "B" * 149  # 300 chars, space in middle
    tag = ClaimTag(
        claim_id="test-wrap",
        claim_text="Some claim.",
        bucket="B",
        timestamp="2026-07-02T00:00:00+00:00",
        criteria_evidence=[
            CriterionEvidence(
                criterion_name="test_criterion",
                criterion_text="Short criterion.",
                evidence_text=long_evidence,
                evidence_source_url="https://example.com",
                evidence_source_type="official",
            )
        ],
    )
    out = format_tag(tag)
    # All characters must appear somewhere (no truncation) — count, not contiguous run
    flat = out.replace("\n", "").replace(" ", "")
    assert flat.count("A") >= 150
    assert flat.count("B") >= 149
    # No line exceeds 80 chars
    for line in out.splitlines():
        assert len(line) <= 80, f"Line too long ({len(line)}): {line!r}"


def test_none_evidence_does_not_raise():
    tag = ClaimTag(
        claim_id="test-none",
        claim_text="A claim with no evidence.",
        bucket="A",
        timestamp="2026-07-02T00:00:00+00:00",
    )
    out = format_tag(tag)
    assert isinstance(out, str)
    assert "not available" in out.lower() or "no" in out.lower()
