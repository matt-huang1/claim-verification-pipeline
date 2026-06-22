"""
Tests for quote_match.py.

These tests lock in the ambiguity-detection behavior designed and manually
verified during development: status is determined by the GAP between the
#1 and #2 candidate scores, not the absolute score of the #1 match alone.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from quote_match import match_quote


def test_specific_unique_quote_in_realistic_document():
    """
    The actual real-world case this module exists for: TSMC's September
    2023 press release, which contains multiple years and percentages
    close together, but the specific claimed quote is long and specific
    enough to be uniquely and confidently located.
    """
    document = """
    HSINCHU, Taiwan, R.O.C., Sep. 15, 2023 - To respond to climate change and
    mitigate climate impact, TSMC (TWSE: 2330, NYSE: TSM) today announced an
    acceleration of its RE100 sustainability timetable, moving its target for
    100 percent renewable energy consumption for all global operations
    forward to 2040 from 2050. TSMC also raised its 2030 target for
    company-wide renewable energy consumption to 60 percent from 40 percent.
    """
    quote = (
        "moving its target for 100 percent renewable energy consumption "
        "for all global operations forward to 2040 from 2050"
    )
    result = match_quote(quote, document)
    assert result.status == "unique"
    assert result.candidates[0].score > 85.0


def test_genuinely_ambiguous_repeated_phrasing_is_flagged():
    """
    The core ambiguity-detection mechanism: if a claimed quote matches
    several distinct locations in the document almost equally well, the
    result must be "ambiguous", not a silent pick of whichever ranked
    first. This was the central design decision of this module.
    """
    document = """
    Region A revenue is expected to grow by 12 percent in the coming year.
    Region B revenue is expected to grow by 12 percent in the coming year.
    Region C revenue is expected to grow by 12 percent in the coming year.
    """
    quote = "revenue is expected to grow by 12 percent in the coming year"
    result = match_quote(quote, document)
    assert result.status == "ambiguous"
    assert len(result.candidates) == 3
    # All three candidates should be near-identical in score, which is what
    # makes this genuinely ambiguous rather than just "three okay matches".
    scores = [c.score for c in result.candidates]
    assert max(scores) - min(scores) < 5.0


def test_bare_short_quote_is_rejected_before_matching():
    """
    A claimed quote that is too short to be unambiguous in principle (e.g.
    a bare date like "2040") is rejected outright, rather than being passed
    through to fuzzy matching where it could produce a misleadingly
    confident "unique" result against the wrong occurrence.
    """
    result = match_quote("2040", "TSMC will hit its target by 2040.")
    assert result.status == "quote_too_short"
    assert result.candidates == []


def test_no_match_when_document_does_not_contain_anything_similar():
    document = "This document is about an entirely unrelated topic in agriculture."
    quote = "moving its target for 100 percent renewable energy consumption forward"
    result = match_quote(quote, document)
    assert result.status == "no_match"


def test_minor_formatting_differences_still_match():
    """
    Real documents have line breaks and whitespace that won't exactly match
    a cleanly-extracted quote. Fuzzy matching should tolerate this, since
    this is a real, expected difference, not a sign of a wrong match.
    """
    document = "TSMC committed to   100 percent\nrenewable energy by 2040, replacing its earlier 2050 target."
    quote = "TSMC committed to 100 percent renewable energy by 2040"
    result = match_quote(quote, document)
    assert result.status == "unique"
    assert result.candidates[0].score > 80.0


def test_short_exact_match_is_not_penalized_by_window_slack():
    """
    Found via adversarial review (Gemini): a perfect, exact quote sitting
    inside a window padded with window_slack extra characters was being
    scored using fuzz.ratio, which penalizes the match just for the window
    being longer than the quote. Confirmed before the fix: a 15-char exact
    match inside a 35-char window scored 66.7, not 100. Fixed by switching
    to fuzz.partial_ratio.
    """
    document = "...and so Revenue grew 5% in Q3..."
    quote = "Revenue grew 5%"
    result = match_quote(quote, document)
    assert result.status == "unique"
    assert result.candidates[0].score >= 99.0


def test_wrong_but_distinctive_match_is_rejected_not_flagged_unique():
    """
    Found via adversarial review (ChatGPT): a large gap between #1 and #2
    is NOT sufficient evidence that a match is correct. A quote that
    doesn't actually appear in the document can still score noticeably
    higher against one unrelated passage than against the rest of the
    document, producing a large gap despite being a wrong match entirely.
    Confirmed before the fix: "The total revenue grew by 20 percent this
    year" against "The new engineering team grew by 20 people this week"
    scored ~64 with nothing else competing, and the old code (50.0 floor)
    flagged this "unique". MINIMUM_SCORE_FOR_UNIQUE (80.0) fixes this by
    requiring the top match to be strong on its own terms, not just
    dominant relative to weaker competitors.
    """
    document = "The new engineering team grew by 20 people this week."
    quote = "The total revenue grew by 20 percent this year."
    result = match_quote(quote, document)
    assert result.status == "no_match"


def test_mediocre_unique_looking_match_with_no_competition_is_rejected():
    """
    Direct test of the MINIMUM_SCORE_FOR_UNIQUE floor in isolation: even
    with zero competing candidates (a single match, nothing else in the
    document comes close), a weak absolute score must not be called
    'unique'. Uniqueness requires both a real gap AND a confident top
    score - either one alone is insufficient.
    """
    document = "This document mentions nothing similar at all, just one vague reference to growth somewhere."
    quote = "Revenue growth accelerated significantly during the period"
    result = match_quote(quote, document)
    assert result.status in ("no_match",)
