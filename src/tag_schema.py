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
class ReasoningCheckEvidence:
    """
    Placeholder for Bucket B verification: not "is this true" but "is the
    reasoning shown, and is the underlying technical classification (e.g.
    an NZIF alignment tier) correct against the actual framework
    criteria." No upstream check populates this evidence type yet (the
    Bucket B/C/D checks themselves are not built). The tag-layer logic
    here is complete and tested.
    """

    reasoning_shown: bool
    framework_classification_checked: bool
    notes: str


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
    reasoning_evidence: ReasoningCheckEvidence | None = None
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
            if self.reasoning_evidence is None:
                return "incomplete"
            if not (
                self.reasoning_evidence.reasoning_shown
                and self.reasoning_evidence.framework_classification_checked
            ):
                return "reasoning_not_fully_checked"
            return "verified"

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
