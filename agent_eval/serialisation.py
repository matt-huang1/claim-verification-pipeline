"""
serialisation.py

Converts ClaimTag and run_pipeline result dicts to/from plain
JSON-serialisable dicts.

KEY DESIGN DECISIONS:

overall_status is stored in the serialised ClaimTag dict even though it
is a computed property on ClaimTag. It is included so the UI can display
the verdict without reconstructing a ClaimTag object, and because the
value is fully deterministic — the same evidence always produces the
same status. dict_to_tag does NOT restore overall_status from the stored
value; it reconstructs a real ClaimTag and lets the property recompute it.

indicator_results keys are int in Python (dict[int, str]) but JSON only
supports string keys. tag_to_dict coerces them to str; dict_to_tag
converts them back to int on deserialisation.

historical_levels is list[tuple[str, int]] in Python. JSON has no tuple
type, so each pair is stored as a [str, int] list. dict_to_tag
reconstructs each pair as a tuple(str, int).
"""

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
    UnresolvedFinding,
)

# ─── evidence serialisers ─────────────────────────────────────────────────────


def _domain_to_dict(e: DomainCheckEvidence) -> dict:
    return {"domain": e.domain, "passed": e.passed, "matched_entry": e.matched_entry}


def _domain_from_dict(d: dict) -> DomainCheckEvidence:
    return DomainCheckEvidence(
        domain=d["domain"],
        passed=d["passed"],
        matched_entry=d["matched_entry"],
    )


def _quote_to_dict(e: QuoteMatchEvidence) -> dict:
    return {
        "status": e.status,
        "top_score": e.top_score,
        "matched_text": e.matched_text,
        "candidate_count": e.candidate_count,
    }


def _quote_from_dict(d: dict) -> QuoteMatchEvidence:
    return QuoteMatchEvidence(
        status=d["status"],
        top_score=d["top_score"],
        matched_text=d["matched_text"],
        candidate_count=d["candidate_count"],
    )


def _criterion_to_dict(e: CriterionEvidence) -> dict:
    return {
        "criterion_name": e.criterion_name,
        "criterion_text": e.criterion_text,
        "evidence_text": e.evidence_text,
        "evidence_source_url": e.evidence_source_url,
        "evidence_source_type": e.evidence_source_type,
    }


def _criterion_from_dict(d: dict) -> CriterionEvidence:
    return CriterionEvidence(
        criterion_name=d["criterion_name"],
        criterion_text=d["criterion_text"],
        evidence_text=d["evidence_text"],
        evidence_source_url=d["evidence_source_url"],
        evidence_source_type=d["evidence_source_type"],
    )


def _tpi_to_dict(e: TPIManagementQualityEvidence) -> dict:
    return {
        "company_tpi_id": e.company_tpi_id,
        "company_slug": e.company_slug,
        "overall_level": e.overall_level,
        "current_level_date": e.current_level_date,
        "indicator_results": {str(k): v for k, v in e.indicator_results.items()},
        "historical_levels": (
            [[date, lvl] for date, lvl in e.historical_levels]
            if e.historical_levels is not None
            else None
        ),
        "max_level": e.max_level,
    }


def _tpi_from_dict(d: dict) -> TPIManagementQualityEvidence:
    raw_hist = d["historical_levels"]
    return TPIManagementQualityEvidence(
        company_tpi_id=d["company_tpi_id"],
        company_slug=d["company_slug"],
        overall_level=d["overall_level"],
        current_level_date=d["current_level_date"],
        indicator_results={int(k): v for k, v in d["indicator_results"].items()},
        historical_levels=(
            [(row[0], int(row[1])) for row in raw_hist]
            if raw_hist is not None
            else None
        ),
        max_level=d["max_level"],
    )


def _definition_group_to_dict(g: DefinitionGroup) -> dict:
    return {
        "member_source_urls": g.member_source_urls,
        "shared_definition_label": g.shared_definition_label,
        "reasoning": g.reasoning,
    }


def _definition_group_from_dict(d: dict) -> DefinitionGroup:
    return DefinitionGroup(
        member_source_urls=d["member_source_urls"],
        shared_definition_label=d["shared_definition_label"],
        reasoning=d["reasoning"],
    )


def _distinct_to_dict(f: DistinctFinding) -> dict:
    return {"source_url": f.source_url, "reasoning": f.reasoning}


def _distinct_from_dict(d: dict) -> DistinctFinding:
    return DistinctFinding(source_url=d["source_url"], reasoning=d["reasoning"])


def _unresolved_to_dict(f: UnresolvedFinding) -> dict:
    return {"source_url": f.source_url, "reasoning": f.reasoning}


def _unresolved_from_dict(d: dict) -> UnresolvedFinding:
    return UnresolvedFinding(source_url=d["source_url"], reasoning=d["reasoning"])


def _spe_to_dict(e: SourcePluralityEvidence) -> dict:
    return {
        "sources_checked": e.sources_checked,
        "groups": [_definition_group_to_dict(g) for g in e.groups],
        "distinct_sources": [_distinct_to_dict(f) for f in e.distinct_sources],
        "unresolved": [_unresolved_to_dict(f) for f in e.unresolved],
        "no_definition_sources": e.no_definition_sources,
        "failed_reconciliation": e.failed_reconciliation,
        "notes": e.notes,
    }


def _spe_from_dict(d: dict) -> SourcePluralityEvidence:
    return SourcePluralityEvidence(
        sources_checked=d["sources_checked"],
        groups=[_definition_group_from_dict(g) for g in d["groups"]],
        distinct_sources=[_distinct_from_dict(f) for f in d["distinct_sources"]],
        unresolved=[_unresolved_from_dict(f) for f in d["unresolved"]],
        no_definition_sources=d["no_definition_sources"],
        failed_reconciliation=d["failed_reconciliation"],
        notes=d["notes"],
    )


def _assumption_to_dict(a: AssumptionItem) -> dict:
    return {"text": a.text, "present_in_claim": a.present_in_claim}


def _assumption_from_dict(d: dict) -> AssumptionItem:
    return AssumptionItem(text=d["text"], present_in_claim=d["present_in_claim"])


def _causal_step_to_dict(s: CausalStep) -> dict:
    return {"text": s.text, "present_in_claim": s.present_in_claim}


def _causal_step_from_dict(d: dict) -> CausalStep:
    return CausalStep(text=d["text"], present_in_claim=d["present_in_claim"])


def _ase_to_dict(e: AssumptionsStatedEvidence) -> dict:
    return {
        "assumptions": [_assumption_to_dict(a) for a in e.assumptions],
        "causal_steps": [_causal_step_to_dict(s) for s in e.causal_steps],
        "notes": e.notes,
    }


def _ase_from_dict(d: dict) -> AssumptionsStatedEvidence:
    return AssumptionsStatedEvidence(
        assumptions=[_assumption_from_dict(a) for a in d["assumptions"]],
        causal_steps=[_causal_step_from_dict(s) for s in d["causal_steps"]],
        notes=d["notes"],
    )


# ─── public API ───────────────────────────────────────────────────────────────


def tag_to_dict(tag: ClaimTag) -> dict:
    """
    Convert a ClaimTag to a JSON-serialisable dict.

    overall_status is included as a computed snapshot — see module docstring.
    """
    return {
        "claim_id": tag.claim_id,
        "claim_text": tag.claim_text,
        "bucket": tag.bucket,
        "timestamp": tag.timestamp,
        "overall_status": tag.overall_status,
        "domain_evidence": (
            _domain_to_dict(tag.domain_evidence)
            if tag.domain_evidence is not None
            else None
        ),
        "quote_evidence": (
            _quote_to_dict(tag.quote_evidence)
            if tag.quote_evidence is not None
            else None
        ),
        "criteria_evidence": (
            [_criterion_to_dict(c) for c in tag.criteria_evidence]
            if tag.criteria_evidence is not None
            else None
        ),
        "tpi_evidence": (
            _tpi_to_dict(tag.tpi_evidence) if tag.tpi_evidence is not None else None
        ),
        "source_plurality_evidence": (
            _spe_to_dict(tag.source_plurality_evidence)
            if tag.source_plurality_evidence is not None
            else None
        ),
        "assumptions_evidence": (
            _ase_to_dict(tag.assumptions_evidence)
            if tag.assumptions_evidence is not None
            else None
        ),
    }


def dict_to_tag(d: dict) -> ClaimTag:
    """
    Reconstruct a ClaimTag from a dict produced by tag_to_dict.

    overall_status is NOT restored from the stored value — it is recomputed
    from the reconstructed evidence, which is always deterministic.
    """
    de = d.get("domain_evidence")
    qe = d.get("quote_evidence")
    ce = d.get("criteria_evidence")
    te = d.get("tpi_evidence")
    spe = d.get("source_plurality_evidence")
    ae = d.get("assumptions_evidence")

    return ClaimTag(
        claim_id=d["claim_id"],
        claim_text=d["claim_text"],
        bucket=d["bucket"],
        timestamp=d["timestamp"],
        domain_evidence=_domain_from_dict(de) if de is not None else None,
        quote_evidence=_quote_from_dict(qe) if qe is not None else None,
        criteria_evidence=(
            [_criterion_from_dict(c) for c in ce] if ce is not None else None
        ),
        tpi_evidence=_tpi_from_dict(te) if te is not None else None,
        source_plurality_evidence=_spe_from_dict(spe) if spe is not None else None,
        assumptions_evidence=_ase_from_dict(ae) if ae is not None else None,
    )


def result_to_dict(result: dict) -> dict:
    """
    Convert a run_pipeline result dict to a JSON-serialisable dict.

    Calls tag_to_dict on result["tag"] if not None.
    """
    tag = result.get("tag")
    return {
        "outcome": result.get("outcome"),
        "bucket": result.get("bucket"),
        "triage_reasoning": result.get("triage_reasoning"),
        "tag": tag_to_dict(tag) if tag is not None else None,
    }


def dict_to_result(d: dict) -> dict:
    """
    Reconstruct a run_pipeline result dict from one produced by result_to_dict.

    Calls dict_to_tag on d["tag"] if not None.
    """
    tag_d = d.get("tag")
    return {
        "outcome": d.get("outcome"),
        "bucket": d.get("bucket"),
        "triage_reasoning": d.get("triage_reasoning"),
        "tag": dict_to_tag(tag_d) if tag_d is not None else None,
    }
