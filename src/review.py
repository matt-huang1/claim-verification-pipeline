"""
review.py

Presentation-only module — no logic, no decisions, no LLM calls.
Renders a ClaimTag or a run_pipeline result dict as plain text for
terminal output. Not HTML, not markdown.

format_result is the normal entry point when you have a pipeline result
dict (keys: "outcome", "bucket", "triage_reasoning", "tag").
format_tag is for when you already have a ClaimTag directly.
"""

import textwrap

from src.tag_schema import (
    ClaimTag,
    SourcePluralityEvidence,
    TPIManagementQualityEvidence,
    AssumptionsStatedEvidence,
)

_W = 72  # outer wrap width
_WI = 68  # inner wrap width (inside indented blocks)


def _hdr(title: str) -> str:
    body = f"═══ {title} "
    return body + "═" * max(0, 52 - len(body))


def _status_icon(status: str) -> str:
    _PASS = {
        "verified",
        "criteria_evidence_gathered",
        "tpi_data_fetched",
        "disambiguated",
        "assumptions_explicit",
    }
    _FAIL = {
        "source_illegitimate",
        "numeric_mismatch",
        "no_match",
        "assumptions_not_stated",
    }
    if status in _PASS:
        return "✓"
    if status in _FAIL:
        return "✗"
    return "⚠"


def _wrap(text: str, width: int, indent: str) -> str:
    """Wrap text to width, applying indent to every line."""
    return textwrap.fill(
        text or "(not available)",
        width=width,
        initial_indent=indent,
        subsequent_indent=indent,
    )


# ─── bucket-specific section renderers ────────────────────────────────────────


def _fmt_bucket_a(tag: ClaimTag) -> str:
    lines = []

    de = tag.domain_evidence
    if de is None:
        lines.append("⚠  Domain evidence: not available")
    else:
        icon = "✓" if de.passed else "✗"
        status_word = "passed" if de.passed else "FAILED"
        lines.append(f"{icon} Domain: {de.domain}  [{status_word}]")
        matched = de.matched_entry or "none"
        lines.append(f"    Matched entry: {matched}")

    lines.append("")
    lines.append(_hdr("QUOTE MATCH"))

    qe = tag.quote_evidence
    if qe is None:
        lines.append("⚠  Quote evidence: not available")
    else:
        icon = (
            "✓"
            if qe.status == "unique"
            else "✗" if qe.status in {"numeric_mismatch", "no_match"} else "⚠"
        )
        score = f"{qe.top_score:.4f}" if qe.top_score is not None else "n/a"
        lines.append(f"{icon} Status: {qe.status}  Score: {score}")
        lines.append("  Matched text:")
        matched = qe.matched_text or "none"
        lines.append(_wrap(matched, _W, "    "))

    return "\n".join(lines)


def _fmt_bucket_b_criteria(tag: ClaimTag) -> str:
    lines = []
    ev = tag.criteria_evidence

    if not ev:
        lines.append("⚠  No criteria evidence gathered.")
        return "\n".join(lines)

    lines.append(f"{len(ev)} criteria with verified evidence:")
    for i, ce in enumerate(ev, 1):
        lines.append("")
        lines.append(f"  [{i}] {ce.criterion_name}")
        lines.append("      Criterion:")
        lines.append(_wrap(ce.criterion_text, _WI, "        "))
        lines.append(f"      Evidence [{ce.evidence_source_type}]:")
        lines.append(_wrap(ce.evidence_text, _WI, "        "))
        lines.append(f"      Source: {ce.evidence_source_url}")

    return "\n".join(lines)


def _fmt_bucket_b_tpi(tpi: TPIManagementQualityEvidence) -> str:
    lines = []
    company_tpi_id = tpi.company_tpi_id or "n/a"
    lines.append(f"  Company: {tpi.company_slug}  (TPI ID: {company_tpi_id})")

    level = tpi.overall_level if tpi.overall_level is not None else "n/a"
    date = tpi.current_level_date or "n/a"
    lines.append(f"  Current level: {level} / 5  (assessed {date})")

    if tpi.indicator_results:
        passing = sorted(k for k, v in tpi.indicator_results.items() if v == "yes")
        failing = sorted(k for k, v in tpi.indicator_results.items() if v == "no")
        not_applicable = sorted(
            k for k, v in tpi.indicator_results.items() if v == "not-applicable"
        )
        lines.append("")
        lines.append("  Indicators:")
        p_str = ", ".join(str(n) for n in passing) if passing else "none"
        f_str = ", ".join(str(n) for n in failing) if failing else "none"
        lines.append(f"    Passing ({len(passing)}): {p_str}")
        lines.append(f"    Failing ({len(failing)}): {f_str}")
        if not_applicable:
            na_str = ", ".join(str(n) for n in not_applicable)
            lines.append(f"    Not applicable ({len(not_applicable)}): {na_str}")
    else:
        lines.append("  Indicators: not available")

    if tpi.historical_levels:
        lines.append("")
        lines.append("  Historical levels:")
        for date_h, lvl in tpi.historical_levels:
            lines.append(f"    {date_h}: Level {lvl}")
    else:
        lines.append("  Historical levels: not available")

    return "\n".join(lines)


def _fmt_bucket_c(spe: SourcePluralityEvidence) -> str:
    lines = []
    lines.append(f"  Sources checked: {spe.sources_checked}")

    if spe.groups:
        lines.append("")
        lines.append(f"  Groups ({len(spe.groups)}):")
        for i, g in enumerate(spe.groups, 1):
            lines.append(f"  [Group {i}] {g.shared_definition_label}")
            lines.append("    Sources:")
            for url in g.member_source_urls:
                lines.append(f"      {url}")
            lines.append(
                _wrap(g.reasoning, _WI, "    Reasoning: ").replace(
                    "    Reasoning: ", "    Reasoning: ", 1
                )
            )

    if spe.distinct_sources:
        lines.append("")
        lines.append(f"  Distinct sources ({len(spe.distinct_sources)}):")
        for df in spe.distinct_sources:
            lines.append(_wrap(f"{df.source_url}: {df.reasoning}", _WI, "    "))

    if spe.unresolved:
        lines.append("")
        lines.append(f"  Unresolved ({len(spe.unresolved)}):")
        for uf in spe.unresolved:
            lines.append(_wrap(f"{uf.source_url}: {uf.reasoning}", _WI, "    "))

    if spe.no_definition_sources:
        lines.append("")
        lines.append(f"  No stated definition ({len(spe.no_definition_sources)}):")
        for url in spe.no_definition_sources:
            lines.append(f"    {url}")

    if spe.failed_reconciliation:
        lines.append("")
        lines.append(f"  Failed reconciliation ({len(spe.failed_reconciliation)}):")
        for url in spe.failed_reconciliation:
            lines.append(f"    {url}")

    if spe.notes:
        lines.append("")
        lines.append(f"  Notes: {spe.notes}")

    return "\n".join(lines)


def _fmt_bucket_d(ae: AssumptionsStatedEvidence) -> str:
    lines = []

    lines.append(f"  Assumptions ({len(ae.assumptions)}):")
    if ae.assumptions:
        for a in ae.assumptions:
            icon = "✓" if a.present_in_claim else "✗"
            wrapped = _wrap(a.text, _WI, f"    {icon} ")
            lines.append(wrapped)
            stated = "present in claim" if a.present_in_claim else "NOT stated in claim"
            lines.append(f"         [{stated}]")
    else:
        lines.append("    (none identified)")

    lines.append("")
    lines.append(f"  Causal steps ({len(ae.causal_steps)}):")
    if ae.causal_steps:
        for s in ae.causal_steps:
            icon = "✓" if s.present_in_claim else "✗"
            wrapped = _wrap(s.text, _WI, f"    {icon} ")
            lines.append(wrapped)
            stated = "present in claim" if s.present_in_claim else "NOT stated in claim"
            lines.append(f"         [{stated}]")
    else:
        lines.append("    (none identified)")

    if ae.notes:
        lines.append("")
        lines.append(f"  Notes: {ae.notes}")

    return "\n".join(lines)


# ─── public API ───────────────────────────────────────────────────────────────


def format_tag(tag: ClaimTag) -> str:
    """Render one ClaimTag as a readable multi-line string. Never raises."""
    try:
        status = tag.overall_status
    except Exception:
        status = "unknown"

    icon = _status_icon(status)
    ts = getattr(tag, "timestamp", None) or "n/a"

    lines = []

    # CLAIM
    lines.append(_hdr("CLAIM"))
    lines.append(_wrap(tag.claim_text or "(not available)", _W, "  "))
    lines.append("")

    # RESULT
    lines.append(_hdr("RESULT"))
    lines.append(f"  {icon} Status: {status}")

    bucket = getattr(tag, "bucket", None) or "?"
    if bucket == "A":
        lines.append("  → Bucket: A (single authoritative source)")
    elif bucket == "B":
        if tag.tpi_evidence is not None:
            lines.append("  → Bucket: B (TPI Management Quality — direct fetch)")
        else:
            lines.append("  → Bucket: B (framework judgment — human review required)")
    elif bucket == "C":
        lines.append("  → Bucket: C (definitionally fuzzy — source plurality)")
    elif bucket == "D":
        lines.append("  → Bucket: D (counterfactual/future — reasoning check)")
    else:
        lines.append(f"  → Bucket: {bucket}")

    lines.append(f"  → Checked: {ts}")
    lines.append("")

    # Evidence section
    if bucket == "A":
        lines.append(_hdr("SOURCE CHECK"))
        lines.append(_fmt_bucket_a(tag))

    elif bucket == "B":
        if tag.tpi_evidence is not None:
            lines.append(_hdr("TPI MANAGEMENT QUALITY"))
            lines.append(_fmt_bucket_b_tpi(tag.tpi_evidence))
        else:
            lines.append(_hdr("NZIF CRITERIA EVIDENCE"))
            if tag.criteria_evidence is None:
                lines.append("⚠  No criteria evidence gathered.")
            else:
                lines.append(_fmt_bucket_b_criteria(tag))

    elif bucket == "C":
        lines.append(_hdr("SOURCE RECONCILIATION"))
        if tag.source_plurality_evidence is None:
            lines.append("⚠  No source plurality evidence available.")
        else:
            lines.append(_fmt_bucket_c(tag.source_plurality_evidence))

    elif bucket == "D":
        lines.append(_hdr("ASSUMPTIONS & CAUSAL CHAIN"))
        if tag.assumptions_evidence is None:
            lines.append("⚠  No assumptions evidence available.")
        else:
            lines.append(_fmt_bucket_d(tag.assumptions_evidence))

    return "\n".join(lines)


def format_result(result: dict) -> str:
    """
    Render a run_pipeline result dict as a readable multi-line string.

    Expected keys: "outcome", "bucket", "triage_reasoning", "tag".
    Calls format_tag internally if result["tag"] is not None.
    """
    outcome = result.get("outcome") or "n/a"
    bucket = result.get("bucket") or "none (triage failed)"
    triage = result.get("triage_reasoning") or "skipped / not available"
    tag = result.get("tag")

    lines = []
    lines.append(_hdr("PIPELINE RESULT"))
    lines.append(f"  → Outcome:  {outcome}")
    lines.append(f"  → Bucket:   {bucket}")
    lines.append(f"  → Triage:   {triage}")
    lines.append("")

    if tag is not None:
        lines.append(format_tag(tag))
    else:
        lines.append("⚠  No tag produced.")

    return "\n".join(lines)
