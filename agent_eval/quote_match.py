"""Fuzzy quote-vs-document matching with ambiguity detection and a numeric gate.

Checks whether a claimed quote actually appears in a source document. Generic
and fully deterministic: no companies, no claims, no model call — the same
inputs always produce the same result.

Two properties make this a discriminating check rather than a best-match pick:

- Ambiguity is detected, never silently resolved. The top candidates are
  returned with scores, and "unique" vs "ambiguous" is decided by the GAP
  between the #1 and #2 scores, not the #1 score alone.
- The numeric token gate: every number/year/percentage in the claimed quote
  must literally appear in the matched span (exact set comparison) before a
  result can be "unique". Scope limit: this only catches numeric
  hallucinations — a wrong non-numeric word ("REJECTED" vs "APPROVED") still
  passes and needs a different mechanism, deliberately not attempted here.

Candidates are found by calling fuzz.partial_ratio_alignment on the full
document, masking out each match, and searching again — non-overlap is
guaranteed by construction. The design history (four deduplication attempts,
the hallucinated-year bug the numeric gate exists to catch) is recorded in
adr/0003-quote-match.md.
"""

from dataclasses import dataclass
import re

from rapidfuzz import fuzz


def _extract_numeric_tokens(text: str) -> set[str]:
    """
    Extract the load-bearing numeric tokens (years, percentages, dollar
    amounts, plain numbers) from a string. See module docstring,
    "NUMERIC TOKEN GATE", for why this exists.

    The regex requires a decimal point to be followed by digits (so a
    trailing sentence period after a number, e.g. "2050.", is not
    swallowed into the token as "2050." — found and fixed during testing,
    when a genuinely correct quote was failing this gate only because the
    matched span happened to end with a sentence-final period).
    """
    return set(re.findall(r"\d+\.\d+%?|\d+%?", text))


# A small gap between the #1 and #2 match scores means the quote could
# plausibly refer to more than one place in the document. This threshold
# is a percentage-point gap (scores are 0-100), not an absolute score.
# It was not derived from a formula; it is a starting assumption that
# should be revisited against real documents as the system is used.
AMBIGUITY_GAP_THRESHOLD = 10.0

# A claimed quote shorter than this is rejected outright before matching
# is attempted. This exists as a basic guard against degenerate inputs
# (e.g. a bare number like "2040" with no surrounding context), but it is
# NOT the primary defense against ambiguity — the gap-based check above is.
MINIMUM_QUOTE_LENGTH_CHARS = 15

# Minimum absolute score for the #1 candidate to be treated as a real
# match at all, even before the gap check runs. Found via adversarial
# review: a large gap alone does not prove a quote was actually found —
# a quote that doesn't appear anywhere can still score noticeably higher
# against one unrelated passage than the rest of the document, producing
# a large gap despite not being a real match.
MINIMUM_SCORE_FOR_UNIQUE = 80.0

# Small safety margin added around each recovered match span. Not a
# window-size guess (see module docstring, Attempt 4) — the match
# position is already correctly found; this just protects against
# partial_ratio_alignment clipping a trailing token by a few characters.
PAD_CHARS = 15

# Character used to mask out an already-found match before searching for
# the next-best candidate. Chosen to be a character that will never
# plausibly appear in real source text or match anything.
_MASK_CHAR = "\x00"


@dataclass
class MatchCandidate:
    """A single candidate match found in the source document."""

    text: str
    score: float
    # start_index and end_index are absolute positions, in the SOURCE
    # DOCUMENT, of the actual matched substring (including padding).
    start_index: int
    end_index: int


@dataclass
class QuoteMatchResult:
    """
    The full result of attempting to match a claimed quote against a
    source document.

    status is one of:
        "unique"      - top match clearly distinguishable from the rest,
                         AND every numeric token in the claimed quote is
                         present in the matched span
        "ambiguous"   - top matches are too close in score to distinguish
        "no_match"    - nothing in the document came close to the quote
        "numeric_mismatch" - the matched span looked textually strong, but
                         a number/year/percentage in the claimed quote does
                         not actually appear in the matched span
        "quote_too_short" - the claimed quote was rejected before matching
                             was attempted, because it was too short to be
                             unambiguous even in principle
    """

    status: str
    candidates: list[MatchCandidate]
    claimed_quote: str


def _find_top_candidates(
    quote: str, document: str, max_candidates: int = 3
) -> list[MatchCandidate]:
    """
    Find up to `max_candidates` distinct, non-overlapping matches for
    `quote` within `document`, ranked by score.

    Uses fuzz.partial_ratio_alignment directly on the document, then masks
    out each found match before searching again. This guarantees no
    overlap between returned candidates BY CONSTRUCTION — there is no
    separate deduplication step needed, because a masked region simply
    cannot be matched again.
    """
    working_doc = document
    candidates: list[MatchCandidate] = []

    for _ in range(max_candidates):
        alignment = fuzz.partial_ratio_alignment(quote, working_doc)
        if alignment is None or alignment.score <= 0:
            break

        padded_start = max(0, alignment.dest_start - PAD_CHARS)
        padded_end = min(len(document), alignment.dest_end + PAD_CHARS)

        candidates.append(
            MatchCandidate(
                text=document[padded_start:padded_end],
                score=alignment.score,
                start_index=padded_start,
                end_index=padded_end,
            )
        )

        # Mask out the matched region (not the padding) so the next
        # search call is forced to find a DIFFERENT location.
        working_doc = (
            working_doc[: alignment.dest_start]
            + _MASK_CHAR * (alignment.dest_end - alignment.dest_start)
            + working_doc[alignment.dest_end :]
        )

    return candidates


def match_quote(quote: str, document: str) -> QuoteMatchResult:
    """
    Attempt to find `quote` inside `document`, returning the top 3
    candidate matches with scores, and an explicit status describing
    whether the match is unique, ambiguous, or otherwise unverifiable.

    Args:
        quote: the claimed quote to verify, as extracted by an upstream
               (AI) extraction step.
        document: the full text of the retrieved source document.

    Returns:
        A QuoteMatchResult with status, the top candidates, and the
        original claimed quote (for traceability in the verification tag).
    """
    if len(quote.strip()) < MINIMUM_QUOTE_LENGTH_CHARS:
        return QuoteMatchResult(
            status="quote_too_short", candidates=[], claimed_quote=quote
        )

    top_candidates = _find_top_candidates(quote, document)

    if not top_candidates:
        return QuoteMatchResult(status="no_match", candidates=[], claimed_quote=quote)

    if top_candidates[0].score < 50.0:
        return QuoteMatchResult(
            status="no_match", candidates=top_candidates, claimed_quote=quote
        )

    if len(top_candidates) == 1:
        if top_candidates[0].score < MINIMUM_SCORE_FOR_UNIQUE:
            return QuoteMatchResult(
                status="no_match", candidates=top_candidates, claimed_quote=quote
            )
        status = "unique"
    else:
        gap = top_candidates[0].score - top_candidates[1].score
        if top_candidates[0].score < MINIMUM_SCORE_FOR_UNIQUE:
            return QuoteMatchResult(
                status="no_match", candidates=top_candidates, claimed_quote=quote
            )
        status = "unique" if gap >= AMBIGUITY_GAP_THRESHOLD else "ambiguous"

    if status == "unique":
        if not _extract_numeric_tokens(quote).issubset(
            _extract_numeric_tokens(top_candidates[0].text)
        ):
            status = "numeric_mismatch"

    return QuoteMatchResult(
        status=status, candidates=top_candidates, claimed_quote=quote
    )
