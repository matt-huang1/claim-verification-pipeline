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
and explicitly flags whether the result is "unique" or "ambiguous" based on
the GAP between the #1 and #2 scores, not the absolute score of the #1
match alone. A large gap means the quote was specific enough to be uniquely
located in the document. A small gap means multiple places in the document
match almost equally well, and the system cannot tell which one the claim
actually refers to — that ambiguity must be surfaced, not silently resolved
by picking whichever happened to rank first.

KNOWN OPEN LIMITATION (not yet fixed, found via adversarial review):
ChatGPT's review raised a separate, distinct concern about the
deduplication logic in match_quote (the min_separation = len(quote)
check): it's a crude position-based proxy for "is this the same location
as an existing candidate," not an actual text-overlap check, and could in
principle either wrongly merge two genuinely distinct nearby occurrences,
or wrongly split one long match into several "different" candidates. One
specific counterexample was tested (two distinct claims ~55 characters
apart) and did NOT show the merging failure in that configuration, but
that single negative result does not prove the underlying concern is
unfounded across all spacings. This needs a dedicated test sweeping
across different distances/quote lengths before being considered closed,
not assumed fixed because one case came out fine.
"""

from dataclasses import dataclass

from rapidfuzz import fuzz


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
# This is just a cheap early rejection for obviously-too-short input.
MINIMUM_QUOTE_LENGTH_CHARS = 15

# Minimum absolute score for the #1 candidate to be treated as a real
# match at all, even before the gap check runs. Raised from an earlier
# value of 50.0 after adversarial review found a "wrong but distinctive"
# failure mode: a quote that doesn't actually appear in the document can
# still match one unrelated passage noticeably better than the rest of
# the document (e.g. ~60-65 on a 40-60 char string), producing a large
# GAP even though the "match" itself is not a real match. A large gap
# alone does not prove a quote was found — it only proves one candidate
# dominated the others. This floor exists to catch that case: nothing
# below 80 is treated as confidently "unique", regardless of the gap to
# #2, because below that level the match is not strong enough on its own
# terms to trust, gap or no gap.
MINIMUM_SCORE_FOR_UNIQUE = 80.0


@dataclass
class MatchCandidate:
    """A single candidate match found in the source document."""
    text: str
    score: float
    start_index: int


@dataclass
class QuoteMatchResult:
    """
    The full result of attempting to match a claimed quote against a
    source document.

    status is one of:
        "unique"      - top match clearly distinguishable from the rest
        "ambiguous"   - top matches are too close in score to distinguish
        "no_match"    - nothing in the document came close to the quote
        "quote_too_short" - the claimed quote was rejected before matching
                             was attempted, because it was too short to be
                             unambiguous even in principle
    """
    status: str
    candidates: list[MatchCandidate]
    claimed_quote: str


def _sliding_window_matches(quote: str, document: str, window_slack: int = 20) -> list[MatchCandidate]:
    """
    Find candidate substrings of `document` that are similar in length to
    `quote`, and score each by character-level similarity to `quote`.

    This is a simple sliding window, not a smart sentence-boundary detector
    (deliberately — see module docstring on why clause/sentence detection
    was rejected as the approach here). The window size tracks the quote's
    own length, plus some slack, so it can still match if the real source
    text is a little longer or shorter than the claimed quote (e.g. due to
    minor paraphrasing or formatting differences).
    """
    quote_len = len(quote)
    window_size = quote_len + window_slack
    step = max(1, quote_len // 4)  # coarse step for speed; good enough for this scale

    candidates = []
    for start in range(0, max(1, len(document) - window_size + 1), step):
        window = document[start:start + window_size]
        # IMPORTANT: use partial_ratio, not ratio, here. fuzz.ratio scores
        # the FULL alignment of both strings, so a perfect, exact quote
        # sitting inside a window padded with window_slack extra characters
        # gets penalized just for the window being longer than the quote —
        # found via adversarial review: a 15-char exact match inside a
        # 35-char window scored 66.7 under ratio() instead of 100.
        # partial_ratio finds the best-aligned substring of the window and
        # scores against that, so window padding no longer drags down a
        # genuinely perfect match. Confirmed: the same case scores 100.0
        # under partial_ratio.
        score = fuzz.partial_ratio(quote, window)
        candidates.append(MatchCandidate(text=window.strip(), score=score, start_index=start))

    return candidates


def match_quote(quote: str, document: str) -> QuoteMatchResult:
    """
    Attempt to find `quote` inside `document`, returning the top 3
    candidate matches with scores, and an explicit status describing
    whether the match is unique or ambiguous.

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
            status="quote_too_short",
            candidates=[],
            claimed_quote=quote,
        )

    raw_candidates = _sliding_window_matches(quote, document)

    if not raw_candidates:
        return QuoteMatchResult(status="no_match", candidates=[], claimed_quote=quote)

    # Sort by score, descending, and take the top 3 DISTINCT regions.
    # "Distinct" matters: a sliding window over a long match will produce
    # many near-identical overlapping candidates for the same real location
    # in the document. Without de-duplication, the "top 3" would often just
    # be three overlapping windows over the SAME match, which defeats the
    # purpose of ambiguity detection (that would look like a "unique" result
    # even when it isn't really showing 3 different locations).
    raw_candidates.sort(key=lambda c: c.score, reverse=True)

    top_candidates: list[MatchCandidate] = []
    min_separation = len(quote)  # candidates within one quote-length of each other are "the same" location
    for candidate in raw_candidates:
        if all(abs(candidate.start_index - existing.start_index) >= min_separation for existing in top_candidates):
            top_candidates.append(candidate)
        if len(top_candidates) == 3:
            break

    if not top_candidates:
        return QuoteMatchResult(status="no_match", candidates=[], claimed_quote=quote)

    if top_candidates[0].score < 50.0:
        # Even the best match is poor — nothing in the document closely
        # resembles the claimed quote at all.
        return QuoteMatchResult(status="no_match", candidates=top_candidates, claimed_quote=quote)

    if len(top_candidates) == 1:
        if top_candidates[0].score >= MINIMUM_SCORE_FOR_UNIQUE:
            return QuoteMatchResult(status="unique", candidates=top_candidates, claimed_quote=quote)
        return QuoteMatchResult(status="no_match", candidates=top_candidates, claimed_quote=quote)

    gap = top_candidates[0].score - top_candidates[1].score

    # A large gap alone is NOT sufficient evidence of a real match — found
    # via adversarial review: a quote that matches nothing well can still
    # score noticeably higher than everything else in the document (e.g.
    # ~60-65 with nothing else competing), producing a large gap despite
    # not actually appearing anywhere. The gap only tells you one candidate
    # dominated the others; it says nothing about whether that candidate
    # is actually correct. MINIMUM_SCORE_FOR_UNIQUE enforces that "unique"
    # additionally requires the #1 match to be strong on its own terms.
    if top_candidates[0].score < MINIMUM_SCORE_FOR_UNIQUE:
        return QuoteMatchResult(status="no_match", candidates=top_candidates, claimed_quote=quote)

    status = "unique" if gap >= AMBIGUITY_GAP_THRESHOLD else "ambiguous"

    return QuoteMatchResult(status=status, candidates=top_candidates, claimed_quote=quote)
