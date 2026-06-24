"""
Tests for quote_match.py.

These tests lock in behavior found and fixed across two rounds of
adversarial multi-model review (see quote_match.py module docstring for
the full design history). Each test is tied to a specific confirmed
counterexample, not a generic case, so a future regression on any one
of these specific failure modes will be caught.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from quote_match import match_quote


def test_specific_unique_quote_in_realistic_document():
    """The real-world motivating case: TSMC's actual press release text."""
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
    assert result.candidates[0].score > 90.0


def test_genuinely_ambiguous_repeated_phrasing_is_flagged():
    """
    Core ambiguity-detection mechanism: identical claims in 3 distinct
    document locations must be flagged ambiguous, not silently resolved.
    """
    document = """
    Region A revenue is expected to grow by 12 percent in the coming year.
    Region B revenue is expected to grow by 12 percent in the coming year.
    Region C revenue is expected to grow by 12 percent in the coming year.
    """
    quote = "revenue is expected to grow by 12 percent in the coming year"
    result = match_quote(quote, document)
    assert result.status == "ambiguous"
    assert len(result.candidates) >= 2
    scores = [c.score for c in result.candidates[:2]]
    assert max(scores) - min(scores) < 5.0


def test_bare_short_quote_is_rejected_before_matching():
    result = match_quote("2040", "TSMC will hit its target by 2040.")
    assert result.status == "quote_too_short"
    assert result.candidates == []


def test_no_match_when_document_does_not_contain_anything_similar():
    document = "This document is about an entirely unrelated topic in agriculture."
    quote = "moving its target for 100 percent renewable energy consumption forward"
    result = match_quote(quote, document)
    assert result.status == "no_match"


def test_minor_formatting_differences_still_match():
    document = "TSMC committed to   100 percent\nrenewable energy by 2040, replacing its earlier 2050 target."
    quote = "TSMC committed to 100 percent renewable energy by 2040"
    result = match_quote(quote, document)
    assert result.status == "unique"
    assert result.candidates[0].score > 80.0


def test_short_exact_match_is_not_penalized_by_window_slack():
    """
    Round 1 fix (Gemini): a perfect, exact quote sitting inside a padded
    window must not be penalized just for the window being longer than
    the quote. Uses partial_ratio, not ratio.
    """
    document = "...and so Revenue grew 5% in Q3..."
    quote = "Revenue grew 5%"
    result = match_quote(quote, document)
    assert result.status == "unique"
    assert result.candidates[0].score >= 99.0


def test_wrong_but_distinctive_match_is_rejected_not_flagged_unique():
    """
    Round 1 fix (ChatGPT): a large gap alone is not sufficient evidence of
    a real match. A wrong quote matching one unrelated passage better than
    the rest of the document must not be flagged "unique" just because
    nothing else competes with it.
    """
    document = "The new engineering team grew by 20 people this week."
    quote = "The total revenue grew by 20 percent this year."
    result = match_quote(quote, document)
    assert result.status == "no_match"


def test_mediocre_unique_looking_match_with_no_competition_is_rejected():
    document = "This document mentions nothing similar at all, just one vague reference to growth somewhere."
    quote = "Revenue growth accelerated significantly during the period"
    result = match_quote(quote, document)
    assert result.status == "no_match"


def test_self_collision_does_not_produce_false_ambiguity():
    """
    Round 2 fix (Gemini): a single, perfectly isolated, unambiguous quote
    must not be split into two "different" candidates just because two
    overlapping sliding windows (one padded before the match, one padded
    after) both score 100 and happen to clear a position-distance-based
    deduplication check. This was a confirmed bug under the old
    position-distance dedup logic: a quote padded by 20 unrelated
    characters on each side produced status="ambiguous" with two
    candidates both scoring 100.0. Fixed by deduplicating on the actual
    matched character span (via partial_ratio_alignment) rather than
    window start-position distance — both windows resolve to the SAME
    real span in the document, so they correctly collapse to one
    candidate now.
    """
    quote = "12345678901234567890"
    document = "A" * 20 + quote + "B" * 20
    result = match_quote(quote, document)
    assert result.status == "unique"
    assert len(result.candidates) == 1
    assert result.candidates[0].score == 100.0


def test_close_together_different_claims_are_both_surfaced():
    """
    This case was previously documented as an unresolved, deliberately
    unfixed limitation (see git history) when deduplication relied on
    position distance or whole-window text similarity — both proxies
    produced near-identical numbers for "two different claims close
    together" and "one real match seen twice", making them
    indistinguishable.

    Switching to real span-overlap deduplication resolved this as a side
    effect: the actual matched span for "net zero by 2040" and "net zero
    by 2050" do not overlap in the source document, so both are now
    correctly surfaced as distinct, non-overlapping candidates, and the
    real conflicting text (2040 vs 2050) is visible to a human reviewer
    rather than hidden inside a messy overlapping-window text blob.
    """
    document = (
        "The company has committed to net zero by 2040, but separately "
        "notes it expects net zero by 2050 in another division entirely speaking."
    )
    quote = "net zero by 2040"
    result = match_quote(quote, document)
    assert result.status == "ambiguous"
    all_text = " ".join(c.text for c in result.candidates)
    assert "2040" in all_text
    assert "2050" in all_text


def test_real_match_spans_are_recoverable_for_auditing():
    """
    Candidates must expose the actual character span of the match in the
    source document (start_index, end_index), not just a window's start
    position. This is what makes the verification tag traceable back to
    an exact location in the source — required for the facts-and-figures
    page to be auditable, not just "we checked it, trust us."
    """
    document = "TSMC committed to 100 percent renewable energy by 2040."
    quote = "100 percent renewable energy by 2040"
    result = match_quote(quote, document)
    assert result.status == "unique"
    c = result.candidates[0]
    assert document[c.start_index:c.end_index] == c.text


def test_hallucinated_number_is_caught_as_numeric_mismatch():
    """
    The most serious bug found across all review rounds: character-level
    similarity cannot distinguish a correct quote from one where the AI
    changed the single most load-bearing token. A claimed year of "2035"
    that appears NOWHERE in a document actually saying "2040" previously
    scored 97%+ and was flagged "unique". The numeric token gate catches
    this: every number/year in the claimed quote must literally appear in
    the matched span.
    """
    document = "TSMC announced moving its target for 100 percent renewable energy consumption forward to 2040 from 2050, according to the company."
    hallucinated_quote = "moving its target for 100 percent renewable energy consumption forward to 2035"
    result = match_quote(hallucinated_quote, document)
    assert result.status == "numeric_mismatch"

    true_quote = "moving its target for 100 percent renewable energy consumption forward to 2040"
    result2 = match_quote(true_quote, document)
    assert result2.status == "unique"
