"""
quote_match.py

Checks whether a claimed quote actually appears in a source document's text,
using fuzzy substring matching. This module knows nothing about companies,
claims, dates, or buckets — it only knows strings. Generic by design, for
the same reason domain_check.py is generic: the same function should work
for any claim, against any document, regardless of what the claim is about.

This is a deterministic check in the sense that matters: given the same
quote and the same document text, the result is always identical. There is
no model call inside this function, no judgment, no randomness. The scores
it produces are a pure computation (character-level similarity), not a
probability estimate from a language model.

KEY DESIGN DECISION — ambiguity detection, not just "best match":

A naive version of this function would return only the single best-matching
substring. That has a real failure mode: if the AI extracts a quote that is
too short or generic (e.g. just "2040" in a document that mentions several
different dates), the "best match" could be any one of several places in
the document that match almost equally well, and a single silent pick would
hide that ambiguity entirely.

This function always returns the top 3 candidate matches with their scores,
and explicitly flags the result as "unique" or "ambiguous" based on the GAP
between the #1 and #2 scores, not the absolute score of the #1 match alone.

DESIGN HISTORY — four attempts, the last one is the simplification:

Attempt 1: hand-rolled sliding window, deduplicated by position distance.
A proxy for "same real match", not the real thing. Adversarial review
found a confirmed self-collision bug: a single isolated quote could be
split into two "different" candidates that both scored 100, because two
overlapping windows (one padded before the match, one after) cleared the
position-distance dedup check despite covering the same real text.

Attempt 2: deduplicate by whole-window text similarity instead of position.
Tested directly: didn't fix self-collision, and made the close-claims
problem worse (silently merged "2040" and "2050" into one candidate).

Attempt 3: keep the hand-rolled sliding window, but recover the actual
matched span via fuzz.partial_ratio_alignment and deduplicate by real
span overlap. This fixed both attempt-1 problems correctly. But while
verifying it against real press-release text, a separate bug surfaced:
the FIXED WINDOW SIZE could truncate a genuine multi-number match before
reaching all the numbers, depending on exactly where incidental
formatting (a newline, even just different indentation) fell relative to
the window boundaries. Tuning window_slack and step size did not fix
this reliably — results were not monotonic with slack, and a combination
that fixed one test string did not generalize to a near-identical one.

Attempt 4 (current): stop hand-rolling windows entirely. Call
fuzz.partial_ratio_alignment ONCE directly on the full document — this is
what the library is actually designed to do, and it finds the
best-aligned substring without needing a window size to be guessed in
advance. To find multiple distinct candidates (for ambiguity detection),
mask out each match after finding it and search again, which guarantees
no overlap by construction rather than needing a separate dedup function.
A small fixed padding (PAD_CHARS) is added around each recovered span,
because partial_ratio_alignment's matched span is bounded to the length
of the shorter string (the quote), so it can clip a trailing token by a
few characters when the source text has minor whitespace differences.
This padding is NOT a re-introduction of the old window-size guessing —
the match position itself is already correctly found; the padding is
just a small safety margin around a position that is already known to
be right, which is a fundamentally more robust kind of slack than trying
to guess where an unknown match might fall.

NUMERIC TOKEN GATE:

Found via adversarial review — the single most serious bug found in this
module: character-level similarity (fuzz.partial_ratio) cannot
distinguish a correct quote from one where the AI changed the single
most load-bearing token (a year, a percentage, a dollar figure). A
hallucinated year that appears NOWHERE in the source document still
scored 97%+ and was flagged "unique" before this gate existed — the
project's own named failure mode, non-discriminating verification,
demonstrated inside its own verification tool.

Every number/year/percentage in the claimed quote must literally appear
in the matched span, checked by exact set comparison, not fuzzy
similarity, before a result can be "unique". SCOPE LIMIT, stated
explicitly: this only catches hallucinations where the wrong token is
numeric. A claim like "the board REJECTED the proposal" vs a source
that says "APPROVED" would still pass, since neither word is a number.
That class of error needs a different mechanism and is not attempted here.
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
        if alignment.score <= 0:
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
