"""
Tests for tag_schema.py.

The central thing being tested here is not any individual field, but
whether overall_status can ever be "verified" without the actual evidence
that justifies it - that is the entire reason this module exists in this
shape (one tag per claim, typed evidence slots, a computed property
rather than a settable field).
"""

from tag_schema import (
    AssumptionsStatedEvidence,
    ClaimTag,
    CriterionEvidence,
    DefinitionGroup,
    DistinctFinding,
    DomainCheckEvidence,
    QuoteMatchEvidence,
    SourcePluralityEvidence,
    TPIManagementQualityEvidence,
)


def _passing_domain_evidence():
    return DomainCheckEvidence(
        domain="pr.tsmc.com", passed=True, matched_entry="tsmc.com"
    )


def _failing_domain_evidence():
    return DomainCheckEvidence(
        domain="tsmc.com.evil.com", passed=False, matched_entry=None
    )


def _unique_quote_evidence():
    return QuoteMatchEvidence(
        status="unique", top_score=99.1, matched_text="...", candidate_count=1
    )


def test_bucket_a_verified_requires_both_checks_present_and_passing():
    tag = ClaimTag(
        claim_id="c1",
        claim_text="TSMC renewable target",
        bucket="A",
        domain_evidence=_passing_domain_evidence(),
        quote_evidence=_unique_quote_evidence(),
    )
    assert tag.overall_status == "verified"


def test_bucket_a_with_only_quote_evidence_is_incomplete_not_verified():
    """
    The core design point: a quote match alone, even a perfect one, must
    never be enough to call a claim verified. Domain legitimacy is a
    separate, required piece of evidence.
    """
    tag = ClaimTag(
        claim_id="c2",
        claim_text="TSMC renewable target",
        bucket="A",
        quote_evidence=_unique_quote_evidence(),
        # domain_evidence intentionally omitted
    )
    assert tag.overall_status == "incomplete"
    assert tag.overall_status != "verified"


def test_bucket_a_with_only_domain_evidence_is_incomplete_not_verified():
    tag = ClaimTag(
        claim_id="c3",
        claim_text="TSMC renewable target",
        bucket="A",
        domain_evidence=_passing_domain_evidence(),
        # quote_evidence intentionally omitted
    )
    assert tag.overall_status == "incomplete"


def test_spoofed_domain_with_perfect_quote_match_is_not_verified():
    """
    The case this whole design exists to prevent: a domain-spoofing
    attack paired with a textually perfect quote match. If domain check
    and quote match were separate, unlinked tags, a reviewer looking only
    at the quote-match tag would see "unique, 100.0" and might assume the
    claim is solid. Bundled into one tag, the failed domain check
    overrides the perfect quote score.
    """
    tag = ClaimTag(
        claim_id="c4",
        claim_text="TSMC renewable target",
        bucket="A",
        domain_evidence=_failing_domain_evidence(),
        quote_evidence=QuoteMatchEvidence(
            status="unique", top_score=100.0, matched_text="...", candidate_count=1
        ),
    )
    assert tag.overall_status == "source_illegitimate"
    assert tag.overall_status != "verified"


def test_legitimate_domain_with_hallucinated_quote_is_not_verified():
    """The mirror case: a real source, but the quote match itself flags a problem."""
    tag = ClaimTag(
        claim_id="c5",
        claim_text="TSMC renewable target, wrong year claimed",
        bucket="A",
        domain_evidence=_passing_domain_evidence(),
        quote_evidence=QuoteMatchEvidence(
            status="numeric_mismatch",
            top_score=97.4,
            matched_text="...",
            candidate_count=1,
        ),
    )
    assert tag.overall_status == "numeric_mismatch"


def test_ambiguous_quote_status_surfaces_specifically_not_generically():
    tag = ClaimTag(
        claim_id="c6",
        claim_text="some ambiguous claim",
        bucket="A",
        domain_evidence=_passing_domain_evidence(),
        quote_evidence=QuoteMatchEvidence(
            status="ambiguous", top_score=85.0, matched_text="...", candidate_count=3
        ),
    )
    assert tag.overall_status == "ambiguous"


def _sample_criterion_evidence(**kwargs):
    defaults = dict(
        criterion_name="decarbonisation_plan",
        criterion_text=(
            "The company has a documented decarbonisation plan covering "
            "Scope 1 and 2 emissions with interim targets."
        ),
        evidence_text=(
            "TSMC's Climate Action Plan outlines a roadmap to achieve "
            "net-zero Scope 1 and 2 emissions by 2050 with a 2030 interim "
            "reduction target of 28 percent."
        ),
        evidence_source_url="https://csr.tsmc.com/download/csr/2023_csr_en.pdf",
        evidence_source_type="official",
    )
    defaults.update(kwargs)
    return CriterionEvidence(**defaults)


def test_bucket_b_with_criteria_evidence_returns_evidence_gathered():
    """
    With criteria evidence present, Bucket B returns "criteria_evidence_gathered"
    — not "verified". The system collects evidence for human review; it never
    decides whether criteria are met. This is the same structural distinction as
    Bucket C's "disambiguated" and Bucket D's "assumptions_explicit".
    """
    tag = ClaimTag(
        claim_id="c7",
        claim_text="TSMC has a documented decarbonisation plan",
        bucket="B",
        criteria_evidence=[_sample_criterion_evidence()],
    )
    assert tag.overall_status == "criteria_evidence_gathered"
    assert tag.overall_status != "verified"


def test_bucket_b_incomplete_without_evidence():
    tag = ClaimTag(claim_id="c8", claim_text="unchecked judgment claim", bucket="B")
    assert tag.overall_status == "incomplete"


def test_bucket_b_multiple_criteria_all_captured():
    """
    A single Bucket B claim can be checked against multiple NZIF criteria.
    All are held in the list; overall_status reflects the collection state,
    not a per-criterion verdict.
    """
    tag = ClaimTag(
        claim_id="c9",
        claim_text="TSMC aligns with NZIF transition plan criteria",
        bucket="B",
        criteria_evidence=[
            _sample_criterion_evidence(criterion_name="decarbonisation_plan"),
            _sample_criterion_evidence(
                criterion_name="disclosure",
                criterion_text=(
                    "The company publicly discloses its emissions data "
                    "and transition plan in a standardised format."
                ),
                evidence_text=(
                    "TSMC publishes annual GHG inventory data aligned with "
                    "GHG Protocol in its CSR report."
                ),
            ),
        ],
    )
    assert tag.overall_status == "criteria_evidence_gathered"
    assert len(tag.criteria_evidence) == 2


def test_bucket_b_distinguishes_official_and_third_party_source_type():
    """
    evidence_source_type is explicitly "official" or "third_party" — these
    are never silently treated as equivalent. A company's own disclosure and
    a third party's restatement of it are structurally different kinds of
    evidence. This test confirms the field is present and holds the value set.
    """
    official = _sample_criterion_evidence(evidence_source_type="official")
    third_party = _sample_criterion_evidence(evidence_source_type="third_party")
    assert official.evidence_source_type == "official"
    assert third_party.evidence_source_type == "third_party"
    assert official.evidence_source_type != third_party.evidence_source_type


def _totalenergies_tpi_evidence():
    """
    Real TotalEnergies TPI data as confirmed 2026-06-28. Used as a concrete,
    real-world fixture rather than a fully synthetic one: the actual known
    result (Level 5, failing indicators 21 and 22) is more meaningful than
    arbitrary placeholder values, and was independently cross-checked against
    an RBC analyst document before being used here.
    """
    indicators = {i: "yes" for i in range(1, 24)}
    indicators[21] = "no"
    indicators[22] = "no"
    return TPIManagementQualityEvidence(
        company_tpi_id="1216",
        company_slug="totalenergies",
        overall_level=5,
        current_level_date="15/12/2025",
        indicator_results=indicators,
        historical_levels=[
            ("01/07/2017", 3),
            ("01/07/2018", 4),
            ("01/12/2024", 5),
            ("15/12/2025", 5),
        ],
        max_level=5,
    )


def test_bucket_b_with_tpi_evidence_returns_tpi_data_fetched():
    """
    TPI Management Quality and NZIF criteria are independent claims from
    independent frameworks — confirmed against TotalEnergies's real result:
    "Aligning to a net zero pathway" (NZIF) and "Level 5, failing 21/22"
    (TPI) are two different verdicts, not the same fact restated.

    "tpi_data_fetched" is deliberately distinct from "verified" (Bucket A's
    two-independent-checks-agree outcome) and "criteria_evidence_gathered"
    (Bucket B's AI-proposal-then-checked outcome): TPI data is fetched
    directly from source with no AI claim or check involved — a genuinely
    different mechanism.
    """
    tag = ClaimTag(
        claim_id="tpi1",
        claim_text="TotalEnergies TPI Management Quality assessment",
        bucket="B",
        tpi_evidence=_totalenergies_tpi_evidence(),
    )
    assert tag.overall_status == "tpi_data_fetched"
    assert tag.overall_status != "verified"
    assert tag.overall_status != "criteria_evidence_gathered"


def test_bucket_b_criteria_evidence_still_returns_criteria_evidence_gathered():
    """Regression: adding tpi_evidence must not break existing criteria_evidence behavior."""
    tag = ClaimTag(
        claim_id="tpi2",
        claim_text="TSMC has a documented decarbonisation plan",
        bucket="B",
        criteria_evidence=[_sample_criterion_evidence()],
    )
    assert tag.overall_status == "criteria_evidence_gathered"


def test_bucket_b_neither_evidence_type_returns_incomplete():
    tag = ClaimTag(
        claim_id="tpi3",
        claim_text="unchecked Bucket B claim",
        bucket="B",
    )
    assert tag.overall_status == "incomplete"


def test_bucket_b_both_evidence_types_returns_conflicting_evidence_types():
    """
    Both fields populated on one ClaimTag is a bug: NZIF and TPI are separate
    claims that must live on separate ClaimTags. This guard makes that bug
    loud and named rather than silently hiding one evidence type behind the
    other depending on which branch happens to run first.
    """
    tag = ClaimTag(
        claim_id="tpi4",
        claim_text="conflicting evidence bug",
        bucket="B",
        criteria_evidence=[_sample_criterion_evidence()],
        tpi_evidence=_totalenergies_tpi_evidence(),
    )
    assert tag.overall_status == "conflicting_evidence_types"


def _empty_spe(**kwargs):
    """Minimal SourcePluralityEvidence with all lists empty."""
    defaults = dict(
        sources_checked=0,
        groups=[],
        distinct_sources=[],
        unresolved=[],
        no_definition_sources=[],
        failed_reconciliation=[],
        notes="",
    )
    defaults.update(kwargs)
    return SourcePluralityEvidence(**defaults)


def test_bucket_c_no_group_is_definitional_ambiguity_unresolved():
    """No real group established — at least two sources but none agree."""
    tag = ClaimTag(
        claim_id="c10",
        claim_text="foundry market share",
        bucket="C",
        source_plurality_evidence=_empty_spe(
            sources_checked=2,
            distinct_sources=[
                DistinctFinding(
                    source_url="https://trendforce.com/report",
                    reasoning="uses pure-play definition",
                ),
                DistinctFinding(
                    source_url="https://counterpoint.com/report",
                    reasoning="includes IDM captive capacity",
                ),
            ],
            notes="TrendForce vs Counterpoint, category mismatch unresolved",
        ),
    )
    assert tag.overall_status == "definitional_ambiguity_unresolved"


def test_bucket_c_real_group_is_disambiguated_not_verified():
    """
    Found via review: the first version of this property returned
    "verified" for Bucket C once definitions were reconciled, which
    flattens the exact distinction Bucket D's "assumptions_explicit"
    label exists to preserve, for the same structural reason. Bucket C
    has no single authoritative source to check against by definition
    (that's why it isn't Bucket A) — reconciling multiple sources into a
    real group makes a claim honestly presented, not verified against
    ground truth.
    """
    tag = ClaimTag(
        claim_id="c10b",
        claim_text="foundry market share, reconciled",
        bucket="C",
        source_plurality_evidence=_empty_spe(
            sources_checked=2,
            groups=[
                DefinitionGroup(
                    member_source_urls=[
                        "https://trendforce.com/report",
                        "https://counterpoint.com/report",
                    ],
                    shared_definition_label="pure-play foundry (excl. IDM captive)",
                    reasoning="both explicitly exclude IDM in-house fab capacity",
                )
            ],
            notes="both sources confirmed to use the same foundry definition",
        ),
    )
    assert tag.overall_status == "disambiguated"
    assert tag.overall_status != "verified"


def test_bucket_c_all_distinct_no_group_is_definitional_ambiguity_unresolved():
    """
    Every source is confidently distinct from every other — no pair shares
    a definition. groups is empty, so no disambiguation was achieved.
    """
    tag = ClaimTag(
        claim_id="c10c",
        claim_text="foundry market share",
        bucket="C",
        source_plurality_evidence=_empty_spe(
            sources_checked=3,
            distinct_sources=[
                DistinctFinding(source_url="https://a.com", reasoning="pure-play only"),
                DistinctFinding(source_url="https://b.com", reasoning="includes IDMs"),
                DistinctFinding(
                    source_url="https://c.com", reasoning="wafer capacity basis"
                ),
            ],
        ),
    )
    assert tag.overall_status == "definitional_ambiguity_unresolved"


def test_bucket_c_real_group_plus_failed_reconciliation_is_still_disambiguated():
    """
    A partial LLM failure (some sources in failed_reconciliation) does not
    invalidate a real group established from the other sources. If at least
    one group exists, the outcome is "disambiguated" regardless of what
    failed_reconciliation contains.
    """
    tag = ClaimTag(
        claim_id="c10d",
        claim_text="foundry market share",
        bucket="C",
        source_plurality_evidence=_empty_spe(
            sources_checked=3,
            groups=[
                DefinitionGroup(
                    member_source_urls=[
                        "https://trendforce.com/report",
                        "https://counterpoint.com/report",
                    ],
                    shared_definition_label="pure-play foundry",
                    reasoning="both exclude IDM captive capacity",
                )
            ],
            failed_reconciliation=["https://idc.com/report"],
            notes="IDC could not be reconciled after retries",
        ),
    )
    assert tag.overall_status == "disambiguated"


def test_bucket_d_never_returns_verified_even_when_fully_checked():
    """
    Bucket D claims are never "verified" in the Bucket A sense - no
    fact-check is possible even in principle for a future-facing or
    counterfactual claim. This must remain visibly distinct on the facts
    page, not collapse into the same "verified" label Bucket A uses.
    """
    tag = ClaimTag(
        claim_id="c11",
        claim_text="counterfactual: if TSMC disappeared",
        bucket="D",
        assumptions_evidence=AssumptionsStatedEvidence(
            assumptions_listed=True,
            causal_chain_explicit=True,
            notes="fully reasoned through",
        ),
    )
    assert tag.overall_status == "assumptions_explicit"
    assert tag.overall_status != "verified"


def test_overall_status_cannot_be_set_directly():
    """
    overall_status must be a read-only computed property, not a settable
    field - this is the structural guarantee the whole module exists to
    provide. Attempting to assign to it must raise an error.
    """
    tag = ClaimTag(claim_id="c12", claim_text="x", bucket="A")
    try:
        tag.overall_status = "verified"
        assert False, "overall_status should not be settable"
    except AttributeError:
        pass
