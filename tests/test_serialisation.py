"""
Unit tests for src/serialisation.py — no API calls, no pipeline imports
except tag_schema and serialisation.
"""

import json

from agent_eval.tag_schema import (
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
)
from agent_eval.serialisation import (
    tag_to_dict,
    dict_to_tag,
    result_to_dict,
    dict_to_result,
)

# ─── fixture helpers ───────────────────────────────────────────────────────────

_TS = "2026-07-03T00:00:00+00:00"


def _bucket_a_tag(domain_passed=True, quote_status="unique") -> ClaimTag:
    return ClaimTag(
        claim_id="test-a",
        claim_text="TSMC moving RE target to 2040.",
        bucket="A",
        timestamp=_TS,
        domain_evidence=DomainCheckEvidence(
            domain="tsmc.com",
            passed=domain_passed,
            matched_entry="tsmc.com" if domain_passed else None,
        ),
        quote_evidence=QuoteMatchEvidence(
            status=quote_status,
            top_score=0.97 if quote_status == "unique" else 0.2,
            matched_text="100 percent renewable energy to 2040",
            candidate_count=1,
        ),
    )


def _bucket_b_criteria_tag() -> ClaimTag:
    return ClaimTag(
        claim_id="test-b-crit",
        claim_text="Company X has a credible net zero plan.",
        bucket="B",
        timestamp=_TS,
        criteria_evidence=[
            CriterionEvidence(
                criterion_name="decarbonisation_plan",
                criterion_text="Must have a documented net zero plan.",
                evidence_text="Our plan targets net zero by 2050.",
                evidence_source_url="https://example.com/plan",
                evidence_source_type="official",
            ),
            CriterionEvidence(
                criterion_name="disclosure",
                criterion_text="Must disclose Scope 1, 2, and 3 emissions.",
                evidence_text="We disclose Scope 1, 2, and 3 annually.",
                evidence_source_url="https://example.com/esg",
                evidence_source_type="third_party",
            ),
        ],
    )


def _tpi_indicators(failing=(21, 22)) -> dict:
    d = {i: "yes" for i in range(1, 24)}
    for i in failing:
        d[i] = "no"
    return d


def _bucket_b_tpi_tag() -> ClaimTag:
    return ClaimTag(
        claim_id="test-b-tpi",
        claim_text="TotalEnergies TPI Level 5.",
        bucket="B",
        timestamp=_TS,
        tpi_evidence=TPIManagementQualityEvidence(
            company_tpi_id="1216",
            company_slug="totalenergies",
            overall_level=5,
            current_level_date="15/12/2025",
            indicator_results=_tpi_indicators(),
            historical_levels=[("01/07/2017", 3), ("15/12/2024", 5)],
            max_level=5,
        ),
    )


def _bucket_c_tag() -> ClaimTag:
    return ClaimTag(
        claim_id="test-c",
        claim_text="TSMC has ~60% foundry market share.",
        bucket="C",
        timestamp=_TS,
        source_plurality_evidence=SourcePluralityEvidence(
            sources_checked=3,
            groups=[
                DefinitionGroup(
                    member_source_urls=["https://a.com", "https://b.com"],
                    shared_definition_label="Pure-play foundry market",
                    reasoning="Both exclude IDM in-house capacity.",
                )
            ],
            distinct_sources=[
                DistinctFinding(
                    source_url="https://c.com",
                    reasoning="Includes IDM capacity — different scope.",
                )
            ],
            unresolved=[],
            no_definition_sources=[],
            failed_reconciliation=[],
            notes="",
        ),
    )


def _bucket_d_tag() -> ClaimTag:
    return ClaimTag(
        claim_id="test-d",
        claim_text="Without TSMC the transition stalls.",
        bucket="D",
        timestamp=_TS,
        assumptions_evidence=AssumptionsStatedEvidence(
            assumptions=[
                AssumptionItem(
                    text="TSMC is the sole advanced chip supplier.",
                    present_in_claim=True,
                ),
            ],
            causal_steps=[
                CausalStep(
                    text="TSMC halt → chip shortage → transition delay.",
                    present_in_claim=True,
                ),
            ],
            notes="",
        ),
    )


# ─── round-trip tests ─────────────────────────────────────────────────────────


def test_round_trip_bucket_a_verified():
    tag = _bucket_a_tag()
    rt = dict_to_tag(tag_to_dict(tag))

    assert rt.overall_status == "verified"
    assert rt.claim_id == tag.claim_id
    assert rt.claim_text == tag.claim_text
    assert rt.bucket == tag.bucket
    assert rt.timestamp == tag.timestamp
    assert rt.domain_evidence.domain == "tsmc.com"
    assert rt.domain_evidence.passed is True
    assert rt.domain_evidence.matched_entry == "tsmc.com"
    assert rt.quote_evidence.status == "unique"
    assert rt.quote_evidence.top_score == 0.97
    assert rt.quote_evidence.matched_text == "100 percent renewable energy to 2040"
    assert rt.quote_evidence.candidate_count == 1


def test_round_trip_bucket_b_criteria_evidence():
    tag = _bucket_b_criteria_tag()
    rt = dict_to_tag(tag_to_dict(tag))

    assert rt.overall_status == "criteria_evidence_gathered"
    assert len(rt.criteria_evidence) == 2
    names = [c.criterion_name for c in rt.criteria_evidence]
    assert "decarbonisation_plan" in names
    assert "disclosure" in names
    assert rt.criteria_evidence[0].evidence_source_url == "https://example.com/plan"
    assert rt.criteria_evidence[1].evidence_source_url == "https://example.com/esg"


def test_round_trip_bucket_b_tpi_evidence():
    tag = _bucket_b_tpi_tag()
    rt = dict_to_tag(tag_to_dict(tag))

    assert rt.overall_status == "tpi_data_fetched"
    # indicator_results keys must be ints, not strings
    assert all(isinstance(k, int) for k in rt.tpi_evidence.indicator_results)
    assert rt.tpi_evidence.indicator_results[21] == "no"
    assert rt.tpi_evidence.indicator_results[22] == "no"
    assert rt.tpi_evidence.indicator_results[1] == "yes"
    # historical_levels must be tuples, not lists
    hist = rt.tpi_evidence.historical_levels
    assert hist is not None
    assert all(isinstance(pair, tuple) for pair in hist)
    assert hist[0] == ("01/07/2017", 3)
    assert hist[1] == ("15/12/2024", 5)


def test_round_trip_bucket_c_disambiguated():
    tag = _bucket_c_tag()
    rt = dict_to_tag(tag_to_dict(tag))

    assert rt.overall_status == "disambiguated"
    spe = rt.source_plurality_evidence
    assert spe.sources_checked == 3
    assert len(spe.groups) == 1
    assert spe.groups[0].shared_definition_label == "Pure-play foundry market"
    assert spe.groups[0].member_source_urls == ["https://a.com", "https://b.com"]
    assert len(spe.distinct_sources) == 1
    assert spe.distinct_sources[0].source_url == "https://c.com"
    assert spe.unresolved == []


def test_round_trip_bucket_d_assumptions_explicit():
    tag = _bucket_d_tag()
    rt = dict_to_tag(tag_to_dict(tag))

    assert rt.overall_status == "assumptions_explicit"
    ae = rt.assumptions_evidence
    assert len(ae.assumptions) == 1
    assert ae.assumptions[0].present_in_claim is True
    assert isinstance(ae.assumptions[0].present_in_claim, bool)
    assert len(ae.causal_steps) == 1
    assert ae.causal_steps[0].present_in_claim is True
    assert isinstance(ae.causal_steps[0].present_in_claim, bool)


def test_overall_status_in_serialised_dict():
    tag_verified = _bucket_a_tag(quote_status="unique")
    d_verified = tag_to_dict(tag_verified)
    assert d_verified["overall_status"] == "verified"
    assert d_verified["overall_status"] == tag_verified.overall_status

    tag_fail = _bucket_a_tag(domain_passed=False)
    d_fail = tag_to_dict(tag_fail)
    assert d_fail["overall_status"] == "source_illegitimate"
    assert d_fail["overall_status"] == tag_fail.overall_status


def test_none_evidence_fields_serialise_as_none():
    tag = ClaimTag(
        claim_id="test-none",
        claim_text="A claim with no evidence.",
        bucket="A",
        timestamp=_TS,
    )
    d = tag_to_dict(tag)
    assert d["domain_evidence"] is None
    assert d["quote_evidence"] is None
    assert d["criteria_evidence"] is None
    assert d["tpi_evidence"] is None
    assert d["source_plurality_evidence"] is None
    assert d["assumptions_evidence"] is None

    rt = dict_to_tag(d)
    assert rt.domain_evidence is None
    assert rt.quote_evidence is None
    assert rt.overall_status == "incomplete"


def test_result_to_dict_and_back_with_tag():
    tag = _bucket_a_tag()
    result = {
        "outcome": "verified",
        "bucket": "A",
        "triage_reasoning": "single authoritative source",
        "tag": tag,
    }
    d = result_to_dict(result)
    # Must be JSON-serialisable
    json.dumps(d)

    rt = dict_to_result(d)
    assert rt["outcome"] == "verified"
    assert rt["bucket"] == "A"
    assert rt["triage_reasoning"] == "single authoritative source"
    assert rt["tag"].overall_status == "verified"


def test_result_to_dict_and_back_without_tag():
    result = {
        "outcome": "triage_failed",
        "bucket": None,
        "triage_reasoning": None,
        "tag": None,
    }
    d = result_to_dict(result)
    assert d["tag"] is None

    rt = dict_to_result(d)
    assert rt["tag"] is None
    assert rt["outcome"] == "triage_failed"


def test_serialised_dict_is_json_serialisable():
    for tag in [
        _bucket_a_tag(),
        _bucket_b_criteria_tag(),
        _bucket_b_tpi_tag(),
        _bucket_c_tag(),
        _bucket_d_tag(),
    ]:
        d = tag_to_dict(tag)
        # Must not raise TypeError
        json.dumps(d)
