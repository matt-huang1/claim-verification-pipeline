"""
Tests for tag_schema.py.

The central thing being tested here is not any individual field, but
whether overall_status can ever be "verified" without the actual evidence
that justifies it - that is the entire reason this module exists in this
shape (one tag per claim, typed evidence slots, a computed property
rather than a settable field).
"""

from tag_schema import (
    ClaimTag,
    DomainCheckEvidence,
    QuoteMatchEvidence,
    ReasoningCheckEvidence,
    SourcePluralityEvidence,
    AssumptionsStatedEvidence,
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


def test_bucket_b_verified_requires_reasoning_evidence():
    tag = ClaimTag(
        claim_id="c7",
        claim_text="IIGCC bucket classification",
        bucket="B",
        reasoning_evidence=ReasoningCheckEvidence(
            reasoning_shown=True,
            framework_classification_checked=True,
            notes="checked against real criteria table",
        ),
    )
    assert tag.overall_status == "verified"


def test_bucket_b_incomplete_without_evidence():
    tag = ClaimTag(claim_id="c8", claim_text="unchecked judgment claim", bucket="B")
    assert tag.overall_status == "incomplete"


def test_bucket_b_flags_when_classification_not_actually_checked():
    """
    This is exactly the original TSMC failure: reasoning was shown, but
    the technical framework label itself ("climate solutions bucket")
    was never independently checked against the real NZIF document.
    """
    tag = ClaimTag(
        claim_id="c9",
        claim_text="unchecked bucket label",
        bucket="B",
        reasoning_evidence=ReasoningCheckEvidence(
            reasoning_shown=True,
            framework_classification_checked=False,
            notes="label not verified against source",
        ),
    )
    assert tag.overall_status == "reasoning_not_fully_checked"


def test_bucket_c_requires_definitions_reconciled():
    tag = ClaimTag(
        claim_id="c10",
        claim_text="foundry market share",
        bucket="C",
        source_plurality_evidence=SourcePluralityEvidence(
            sources_checked=2,
            definitions_reconciled=False,
            notes="TrendForce vs Counterpoint, category mismatch unresolved",
        ),
    )
    assert tag.overall_status == "definitional_ambiguity_unresolved"


def test_bucket_c_reconciled_is_disambiguated_not_verified():
    """
    Found via review: the first version of this property returned
    "verified" for Bucket C once definitions were reconciled, which
    flattens the exact distinction Bucket D's "assumptions_explicit"
    label exists to preserve, for the same structural reason. Bucket C
    has no single authoritative source to check against by definition
    (that's why it isn't Bucket A) - reconciling multiple sources makes
    a claim honestly presented, not verified against ground truth.
    """
    tag = ClaimTag(
        claim_id="c10b",
        claim_text="foundry market share, reconciled",
        bucket="C",
        source_plurality_evidence=SourcePluralityEvidence(
            sources_checked=2,
            definitions_reconciled=True,
            notes="both sources confirmed to use the same 'foundry' definition",
        ),
    )
    assert tag.overall_status == "disambiguated"
    assert tag.overall_status != "verified"


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
