"""
tag_schema.py

Defines the verification tag: the single, presentable record for one
claim on the facts-and-figures page, built FROM the results of whichever
checks ran against it (domain_check, quote_match, and future bucket B/C/D
checks), not the checks themselves.

WHY THIS EXISTS, AND WHY IT IS SHAPED THE WAY IT IS:

A claim like "TSMC committed to 100% renewable electricity by 2040" needs
two genuinely different things confirmed before it can be called verified:
that the source is actually TSMC's own domain (domain_check), and that the
exact claimed quote is actually in that document (quote_match). Neither
check alone proves the claim. A spoofed domain with a perfectly matched
quote, or a legitimate domain with a hallucinated quote, are both still
unverified claims.

DESIGN DECISION - one tag per claim, not one tag per check:

If domain_check and quote_match each produced their own separate, free-
floating tag, a human reading the facts page could see "quote match:
unique" and stop there without realizing domain legitimacy was never
checked, or was checked and failed. This is exactly the verification-by-
proximity failure this whole project exists to prevent, relocated from a
document's prose into a fragmented set of tags. Bundling both checks into
one ClaimTag, with a single computed overall_status, makes it structurally
visible whether a claim has been FULLY verified or only partially.

DESIGN DECISION - typed evidence slots, not a generic dict:

A tag whose evidence is a loosely-typed dict could be constructed with
the wrong shape (e.g. a domain_check-shaped dict stored where quote-match
evidence belongs) and nothing would catch it until something tried to
read a field that wasn't there. Explicit dataclasses per evidence type
mean a ClaimTag literally cannot be built with the wrong kind of evidence
in the wrong slot - this is checked by the type system, not by hoping
whoever builds the tag remembers the right shape.

DESIGN DECISION - overall_status is a computed property, not a settable
field:

The whole point of a verification tag is that "verified" cannot be
asserted without the evidence that makes it true. If overall_status were
a plain field, it could be set to "verified" by mistake (or by a future
bug, or by someone taking a shortcut under time pressure) without the
underlying checks actually having passed. Making it a read-only property,
computed FROM the attached evidence every time it's accessed, makes that
specific failure mode structurally impossible rather than merely
discouraged by convention.
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


# Bucket B, C, and D checks are not yet implemented (see project README -
# this is a known, deliberate gap, not an oversight). These are declared
# now, as explicit placeholders, rather than left for later, so that
# ClaimTag.overall_status (below) has to make a real decision about every
# bucket type today, instead of silently defaulting to "unknown" behavior
# for buckets that don't exist yet when someone eventually adds them.


@dataclass(frozen=True)
class CriterionEvidence:
    """
    Per-criterion evidence for Bucket B verification.

    Bucket B claims are not "is this number right" (Bucket A) but "does this
    company's disclosed practice actually satisfy this specific NZIF/TPI
    criterion." Each instance of this class captures the evidence for ONE
    criterion. A Bucket B ClaimTag holds a list of these (one per criterion
    checked), stored in ClaimTag.criteria_evidence.

    Fields:
        criterion_name:      Short identifier for the NZIF criterion being
                             checked (e.g. "decarbonisation_plan",
                             "disclosure", "ambition", "targets"). Used as
                             a key, not for display.
        criterion_text:      The actual wording of this criterion from the
                             real NZIF/TPI framework document — not
                             paraphrased or recalled from memory. This is
                             what a human reads to make the final call on
                             whether the criterion is satisfied.
        evidence_text:       The real source text found that is claimed to
                             satisfy this criterion. Placed alongside
                             criterion_text so a human can assess the match
                             without further navigation.
        evidence_source_url: The URL of the page or document where
                             evidence_text was found.
        evidence_source_type: "official" if the source is the company's own
                             disclosure (annual report, press release, IR
                             page); "third_party" if it is a secondary
                             source (analyst report, news article, NGO
                             database). Never silently treated as
                             equivalent: a company's own claim and a third
                             party's restatement of it are structurally
                             different kinds of evidence.

    Deliberately NO verdict field:

    This dataclass has no "meets_criterion: bool" or equivalent. The system
    collects and presents evidence; a human reads criterion_text and
    evidence_text side by side and decides whether the criterion is met.
    Automating that judgment would reintroduce exactly the non-discriminating
    verification failure this project exists to prevent, relocated from the
    original paragraph-level context into a per-criterion boolean.

    Known, deferred limitation — textual evidence only:

    This structure captures only textual evidence. Some companies present
    criterion-relevant evidence primarily through charts or graphs (e.g. an
    emissions-trajectory chart with no textual equivalent). This is the same
    class of gap as page_fetch.py's deferred table/image extraction, and is
    deliberately not solved now: no real company in this project's ground
    truth has yet presented a criterion's evidence in a form with no textual
    equivalent at all (TSMC's emissions trajectory exists in both chart and
    text form). Revisit if a real case is found where it doesn't.
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

    Stores the complete, real result from tpi_extract.py - all 23 indicator
    results and the full historical assessment trend, not a summarized or
    compressed form. Same decoupling principle as QuoteMatchEvidence: this
    module doesn't import tpi_extract.py's internals, just the shape of data
    a human reviewer needs.

    Deliberately stores FULL detail, not a compact summary (e.g. not "1-20:
    yes, 21-23: no" or a trend "shape" description): a range-based indicator
    summary would silently misrepresent a company with scattered, non-
    clustered failures (no guarantee failures cluster at the end - tested
    against this directly before deciding), and a compressed trend
    description would lose real information (e.g. whether a score ever
    decreased, the true assessment count). Any compact rendering for display
    belongs in a future presentation layer, not in this data structure - the
    same principle CriterionEvidence and QuoteMatchEvidence already follow.

    Fields:
        company_tpi_id: TPI's internal numeric company ID (e.g. "1216")
        company_slug:   the URL slug used to fetch this data (e.g. "totalenergies")
        overall_level:  the company's current Management Quality level (0-5)
        current_level_date: date of TPI's most recent assessment (NOT
                        necessarily when the company first reached this
                        level - see tpi_extract.py's docstring on this
                        distinction)
        indicator_results: dict[int, str], all 23 real "yes"/"no" results,
                        keyed by indicator number
        historical_levels: list[tuple[str, int]], the full (date, level)
                        history from TPI's own chart data, oldest first
        max_level:      the ceiling level TPI's methodology defines (5)

    Deliberately NO verdict field: same principle as CriterionEvidence - this
    is real, deterministic, already-fetched TPI data (not a model's claim
    needing verification), so there's no "is this adequate" judgment to
    automate here. The judgment this evidence informs is a human's to make,
    looking at this evidence alongside NZIF criteria evidence, exactly as
    already done by hand in the real RBC analysis this project supports.
    """

    company_tpi_id: str
    company_slug: str
    overall_level: int
    current_level_date: str | None
    indicator_results: dict[int, str]
    historical_levels: list[tuple[str, int]]
    max_level: int


@dataclass(frozen=True)
class SourcePluralityEvidence:
    """
    Placeholder for Bucket C verification: source plurality plus explicit
    disambiguation of which definition is in use (e.g. "foundry market
    share" vs "pure foundry market share"). No upstream check populates
    this evidence type yet (the Bucket B/C/D checks themselves are not
    built). The tag-layer logic here is complete and tested.
    """

    sources_checked: int
    definitions_reconciled: bool
    notes: str


@dataclass(frozen=True)
class AssumptionsStatedEvidence:
    """
    Placeholder for Bucket D verification: no fact-check is possible even
    in principle, so the standard is whether the causal chain and
    assumptions are explicit, not whether the claim is "true".
    No upstream check populates this evidence type yet (the Bucket B/C/D
    checks themselves are not built). The tag-layer logic here is
    complete and tested.
    """

    assumptions_listed: bool
    causal_chain_explicit: bool
    notes: str


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
            if not self.source_plurality_evidence.definitions_reconciled:
                return "definitional_ambiguity_unresolved"
            # NOT "verified" - Bucket C claims have no single authoritative
            # source to check against, by definition. Reconciling multiple
            # sources and disambiguating which definition is in use makes
            # the claim honestly presented, not verified against ground
            # truth the way Bucket A is. Using "verified" here would
            # flatten exactly the distinction Bucket D's
            # "assumptions_explicit" label exists to preserve, for the same
            # structural reason: found via review, this was an
            # inconsistency in the first version of this property.
            return "disambiguated"

        if self.bucket == "D":
            if self.assumptions_evidence is None:
                return "incomplete"
            if not (
                self.assumptions_evidence.assumptions_listed
                and self.assumptions_evidence.causal_chain_explicit
            ):
                return "assumptions_not_stated"
            # NOT "verified" - Bucket D claims are never "verified" in the
            # Bucket A sense, since no fact-check is possible even in
            # principle.
            return "assumptions_explicit"

        return "unknown_bucket"
