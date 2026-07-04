"""ClaimTag and the typed evidence dataclasses attached to it.

One tag per claim, bundling every check that ran against it. Three structural
decisions carry the module (full reasoning in adr/0004-tag-schema.md):

- One tag per claim, not one tag per check — a reviewer must never see
  "quote match: unique" in isolation and miss that the domain check failed.
- Typed evidence slots, not a generic dict — a tag cannot be built with the
  wrong kind of evidence in the wrong slot; the type system enforces it.
- overall_status is a computed, read-only property — "verified" cannot be
  asserted, by bug or by shortcut, without the evidence that makes it true.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class DomainCheckEvidence:
    """Typed wrapper around domain_check.check_domain()'s result."""

    domain: str
    passed: bool
    matched_entry: str | None


@dataclass(frozen=True)
class QuoteMatchEvidence:
    """
    Typed wrapper around quote_match.match_quote()'s result.

    Stores only what the facts page needs to display and audit, not the
    full QuoteMatchResult object, so this module doesn't need to import
    quote_match.py's internal dataclasses directly. Keeping this module
    decoupled from the internals of each check is deliberate: tag_schema
    should be buildable and testable without importing every check module,
    the same way domain_check.py and quote_match.py don't know about each
    other or about companies/claims/buckets.
    """

    status: str
    # "unique" | "ambiguous" | "no_match" | "numeric_mismatch" | "quote_too_short"
    top_score: float | None
    matched_text: str | None
    candidate_count: int


# Evidence types for Bucket B, C, and D checks. All four bucket pipelines
# populate these types.


@dataclass(frozen=True)
class CriterionEvidence:
    """
    Per-criterion evidence for Bucket B verification. A Bucket B ClaimTag
    holds a list of these (one per criterion checked) in criteria_evidence.

    Fields:
        criterion_name:      Short identifier for the NZIF criterion (e.g.
                             "decarbonisation_plan"). Used as a key.
        criterion_text:      The criterion's wording from the real NZIF
                             framework document — never paraphrased or
                             recalled from memory.
        evidence_text:       The real source text found that is claimed to
                             address this criterion.
        evidence_source_url: Where evidence_text was found.
        evidence_source_type: "official" (the company's own disclosure) or
                             "third_party" (analyst report, news article) —
                             structurally different kinds of evidence, never
                             silently treated as equivalent.

    Deliberately NO verdict field: the system collects evidence; a human
    reads criterion_text and evidence_text side by side and decides.
    Automating that judgment would reintroduce the non-discriminating
    verification failure this project exists to prevent (adr/0009).

    Textual evidence only — the chart-only-evidence gap is named and
    deferred, not solved preemptively (see KNOWN_LIMITATIONS.md).
    """

    criterion_name: str
    criterion_text: str
    evidence_text: str
    evidence_source_url: str
    evidence_source_type: str  # "official" | "third_party"


@dataclass(frozen=True)
class TPIManagementQualityEvidence:
    """
    Evidence for one company's TPI Management Quality assessment.

    Stores the complete, real result from tpi_extract.py — all 23 indicator
    results and the full historical trend, never a compacted summary (a
    range-based summary would silently misrepresent scattered failures;
    compact rendering belongs in a presentation layer — adr/0011).

    Fields:
        company_tpi_id: TPI's internal numeric company ID (e.g. "1216")
        company_slug:   the URL slug used to fetch this data
        overall_level:  the company's current Management Quality level (0-5)
        current_level_date: date of TPI's most recent assessment (NOT
                        necessarily when the company first reached this
                        level — see tpi_extract.py)
        indicator_results: dict[int, str], all 23 real results, keyed by
                        indicator number
        historical_levels: list[tuple[str, int]], the full (date, level)
                        history from TPI's own chart data, oldest first
        max_level:      the ceiling level TPI's methodology defines (5)

    Deliberately NO verdict field: this is real, deterministic, already-
    fetched TPI data, and the judgment it informs is a human's to make.
    """

    company_tpi_id: str | None  # None if TPI's dropdown was absent from the page
    company_slug: str
    overall_level: int | None  # None if "Current level" text not found on page
    current_level_date: str | None
    indicator_results: dict[int, str]
    historical_levels: list[tuple[str, int]] | None  # None if historical fetch failed
    max_level: int | None  # None if historical fetch failed


@dataclass(frozen=True)
class SourceFinding:
    """
    Per-source extraction result for Bucket C verification.

    Captures what one already-fetched source says about the claim: a claimed
    value (if the source states one) and a stated definition of scope (if the
    source defines its market boundary or category). Both fields are
    independently verified via quote_match — the model's proposal is never
    trusted on its word alone.

    Fields:
        source_url:   URL of the page this finding came from.
        source_type:  "official" (company's own domain) or "third_party"
                      (analyst report, news article, etc.). For Bucket C
                      claims, third_party sources are equally valid — the
                      distinction exists so a human reviewer can see the
                      provenance mix across sources at a glance.
        value_found:  True if the model found a claimed value in the source.
        claimed_value: The verbatim text of the value as it appears in the
                      source, or None if value_found is False.
        is_literal_value: True if the claimed value is expressed as a literal
                      digit or percentage (e.g. "60%", "3.2 billion"). False
                      if it appears as a word-stated approximation ("roughly
                      half") or qualitative description ("dominant share").
                      Always False — not None — when value_found is False,
                      so callers never special-case a second "not
                      applicable" sentinel alongside the value_found flag
                      they already have.
        value_verification_status: The quote_match status for claimed_value,
                      or None if value_found is False (verification was never
                      attempted on a value the model never claimed to have).
        definition_found: True if the model found a stated definition or scope
                      description in the source.
        definition_text: The verbatim definition text as it appears in the
                      source, or None if definition_found is False.
        definition_verification_status: The quote_match status for
                      definition_text, or None if definition_found is False.
    """

    source_url: str
    source_type: str  # "official" | "third_party"
    value_found: bool
    claimed_value: str | None
    is_literal_value: bool
    value_verification_status: str | None
    definition_found: bool
    definition_text: str | None
    definition_verification_status: str | None


@dataclass(frozen=True)
class DefinitionGroup:
    """
    Two or more sources judged to share an underlying real-world scope,
    regardless of how differently they word their definition.

    len(member_source_urls) >= 2 is enforced by reconciliation.py's
    validation step — a group with one member is a malformed LLM response,
    not a legitimate outcome.
    """

    member_source_urls: list[str]
    shared_definition_label: str  # human-readable, model-stated
    reasoning: str  # required, never empty


@dataclass(frozen=True)
class DistinctFinding:
    """
    A source confidently judged to use its own, different definition —
    one that cannot be grouped with any other source in this call.
    """

    source_url: str
    reasoning: str  # required, never empty


@dataclass(frozen=True)
class UnresolvedFinding:
    """
    A source whose relationship to the others could not be confidently
    determined. This is a genuine judgment outcome, not a catch-all for
    parsing or formatting failures. A source that states something
    definitional but too vaguely to confidently place goes here; a source
    for which the LLM response was malformed goes into failed_reconciliation
    on SourcePluralityEvidence — these are distinct, never merged.
    """

    source_url: str
    reasoning: str  # required, never empty


@dataclass(frozen=True)
class SourcePluralityEvidence:
    """
    Bucket C verification record.

    Every SourceFinding that entered reconciliation ends up in exactly one
    of five places — groups, distinct_sources, unresolved,
    no_definition_sources, or failed_reconciliation — never dropped, never
    silently merged. The split is:

        no_definition_sources  — sources with definition_found=False
                                 (deterministic, no LLM involved)
        groups                 — 2+ sources sharing a real-world scope
        distinct_sources       — sources confidently using a different scope
        unresolved             — sources where the relationship was unclear
        failed_reconciliation  — sources sent to the LLM but never got a
                                 usable verdict after retries exhausted;
                                 distinct from "unresolved" (which is a
                                 genuine judgment outcome) — this is a
                                 processing failure, not a judgment

    notes is supplementary only — never the sole place a finding is recorded.
    """

    sources_checked: int
    groups: list[DefinitionGroup]
    distinct_sources: list[DistinctFinding]
    unresolved: list[UnresolvedFinding]
    no_definition_sources: list[str]  # source_urls with definition_found=False
    failed_reconciliation: list[str]  # source_urls with no usable LLM verdict
    notes: str


@dataclass(frozen=True)
class AssumptionItem:
    """One assumption identified in the claim text."""

    text: str  # the assumption, as stated or paraphrased
    present_in_claim: bool  # True = explicitly stated; False = unstated/missing


@dataclass(frozen=True)
class CausalStep:
    """One step in the causal chain identified in the claim text."""

    text: str  # the step, e.g. "TSMC absence → no chips"
    present_in_claim: bool  # True = explicitly stated; False = gap/leap


@dataclass(frozen=True)
class AssumptionsStatedEvidence:
    """
    Bucket D verification record. The LLM surfaces what is explicitly
    present and what is missing in the claim's reasoning — it never
    reaches a verdict. The human reviewer reads assumptions and
    causal_steps and decides whether the claim is honest enough to
    include in a report.

    notes is human-only — never populated by the LLM. It is where a
    reviewer records their own judgment after reading the LLM's
    partial reading alongside the original claim.

    Empty lists are valid and correctly produce
    overall_status="assumptions_not_stated" — a claim so thinly
    written that the LLM finds nothing to report is a real, honest
    finding, not an error.
    """

    assumptions: list[AssumptionItem]
    causal_steps: list[CausalStep]
    notes: str  # human-only, never populated by LLM


@dataclass
class ClaimTag:
    """
    The full, presentable verification record for one claim.

    Every field below is required at construction except the evidence
    slots, which default to None - a claim does not necessarily need
    every kind of check (a Bucket D claim, for instance, will never have
    domain_evidence or quote_evidence, only assumptions_evidence).

    overall_status is deliberately NOT a field. See module docstring.
    """

    claim_id: str
    claim_text: str
    bucket: str  # "A" | "B" | "C" | "D"
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    domain_evidence: DomainCheckEvidence | None = None
    quote_evidence: QuoteMatchEvidence | None = None
    criteria_evidence: list[CriterionEvidence] | None = None
    tpi_evidence: TPIManagementQualityEvidence | None = None
    source_plurality_evidence: SourcePluralityEvidence | None = None
    assumptions_evidence: AssumptionsStatedEvidence | None = None

    @property
    def overall_status(self) -> str:
        """
        Computed from the attached evidence every time this is accessed.
        Cannot be set directly, and cannot be "verified" without the
        actual evidence that makes that true.

        Bucket A requires BOTH domain_evidence and quote_evidence to be
        present and passing. Neither check alone proves a Bucket A claim;
        see module docstring for why.
        """
        if self.bucket == "A":
            if self.domain_evidence is None or self.quote_evidence is None:
                return "incomplete"
            if not self.domain_evidence.passed:
                return "source_illegitimate"
            if self.quote_evidence.status != "unique":
                # surfaces the SPECIFIC reason: "ambiguous",
                # "numeric_mismatch", "no_match", or "quote_too_short" -
                # not a generic failure
                return self.quote_evidence.status
            return "verified"

        if self.bucket == "B":
            if self.criteria_evidence is not None and self.tpi_evidence is not None:
                # Both fields populated on one ClaimTag is a bug: NZIF
                # alignment evidence and TPI Management Quality evidence are
                # independent claims from independent frameworks and must live
                # on separate ClaimTags. Surfacing this as a named, visible
                # error is better than silently resolving to whichever check
                # runs first and hiding the other field's existence.
                return "conflicting_evidence_types"
            if self.criteria_evidence is not None:
                if not self.criteria_evidence:
                    return "incomplete"
                # No automated verdict: the system collects evidence, a human
                # reads criterion_text alongside evidence_text and decides.
                # "criteria_evidence_gathered" signals the evidence is ready
                # for human review — parallel to Bucket C's "disambiguated"
                # and Bucket D's "assumptions_explicit", which are also not
                # "verified" in the Bucket A sense.
                return "criteria_evidence_gathered"
            if self.tpi_evidence is not None:
                # Deliberately distinct from "verified" (Bucket A's two-
                # independent-checks-agree outcome) and from
                # "criteria_evidence_gathered" (Bucket B's AI-proposal-then-
                # checked outcome). TPI data is fetched directly from source
                # with no AI claim or check involved — a genuinely different
                # mechanism, not a weaker form of either other status.
                return "tpi_data_fetched"
            return "incomplete"

        if self.bucket == "C":
            if self.source_plurality_evidence is None:
                return "incomplete"
            if len(self.source_plurality_evidence.groups) >= 1:
                # At least one real group (2+ sources sharing a scope) was
                # established. Partial LLM failures (failed_reconciliation)
                # on other sources do not invalidate a real, independently-
                # established group.
                # NOT "verified" — Bucket C has no single authoritative
                # source by definition. A real group makes the claim honestly
                # presented, not verified against ground truth the way
                # Bucket A is.
                return "disambiguated"
            return "definitional_ambiguity_unresolved"

        if self.bucket == "D":
            if self.assumptions_evidence is None:
                return "incomplete"
            has_stated_assumption = any(
                a.present_in_claim for a in self.assumptions_evidence.assumptions
            )
            has_stated_step = any(
                s.present_in_claim for s in self.assumptions_evidence.causal_steps
            )
            if has_stated_assumption and has_stated_step:
                # NOT "verified" - Bucket D claims are never "verified" in the
                # Bucket A sense, since no fact-check is possible even in
                # principle.
                return "assumptions_explicit"
            return "assumptions_not_stated"

        return "unknown_bucket"
