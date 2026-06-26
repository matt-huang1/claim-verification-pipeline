"""
Tests for criterion_evidence.py.

All tests inject a fake llm_fn — no real API calls are made. quote_match runs
for real against the test documents (it is deterministic and needs no mocking).

Test organisation:
  - NZIF_CRITERIA wording lock (golden-file style, guards against silent drift)
  - successful excerpt verification (found=True, quote_match="unique")
  - criterion_not_found handling (found=False, retry with feedback)
  - quote_match rejection (found=True, excerpt not verifiable)
  - malformed LLM response handling (exception; missing field)
  - no-progress / early-stop rule with this module's own stages
  - independence of different criteria (failure on one does not affect another)
"""

from criterion_evidence import (
    NZIF_CRITERIA,
    NZIF_CRITERION_TIERS,
    _CRITERION_NOT_FOUND_FEEDBACK,
    _MALFORMED_LLM_RESPONSE_FEEDBACK,
    find_criterion_evidence,
)

# ---------------------------------------------------------------------------
# NZIF_CRITERIA wording lock
# ---------------------------------------------------------------------------
# These are golden-file / snapshot tests. The expected strings are copied
# verbatim from the corrected, user-transcribed dictionary (2026-06-26).
# Intentional duplication: if NZIF_CRITERIA ever changes, these tests fail
# loudly, forcing the editor to re-verify against the primary source rather
# than assume an edit is harmless. Do not update these strings without going
# back to the real PDF.


def test_nzif_criteria_has_exactly_the_six_real_criteria_no_invented_ones():
    """
    NZIF_CRITERIA must contain exactly the six criteria from the real table,
    no more and no fewer. "capital_allocation" is the invented key from the
    original unverified version — its absence is checked explicitly.
    """
    expected_keys = {
        "ambition",
        "targets",
        "disclosure",
        "governance",
        "decarbonisation_plan",
        "emissions_performance",
    }
    assert set(NZIF_CRITERIA.keys()) == expected_keys
    assert "capital_allocation" not in NZIF_CRITERIA


def test_nzif_criteria_text_matches_verified_primary_source_transcription():
    """
    Each value in NZIF_CRITERIA must exactly equal the wording transcribed
    directly from the IIGCC NZIF 2.0 primary source PDF by the user on
    2026-06-26. Any edit to NZIF_CRITERIA — even a "minor" rewording — must
    be re-verified against the source before this test is updated.
    """
    assert NZIF_CRITERIA["ambition"] == (
        "A long term goal consistent with the global goal of achieving "
        "net zero by 2050."
    )
    assert NZIF_CRITERIA["targets"] == (
        "Short and medium term targets for scope 1, 2 and material scope 3 "
        "emissions in line with science-based ‘net zero’ pathway. These may "
        "be absolute, or intensity based: a) where available, a sectoral "
        "decarbonisation / carbon budget approach should be used; b) minimum "
        "for other assets is a global or regional average pathway."
    )
    assert NZIF_CRITERIA["disclosure"] == (
        "Disclosure of scope 1 and 2 emissions, and disclosure of material "
        "scope 3, in line with regulatory requirements where applicable or "
        "the PCAF Standard."
    )
    assert NZIF_CRITERIA["governance"] == (
        "Governance/management responsibility for targets and decarbonisation plan."
    )
    assert NZIF_CRITERIA["decarbonisation_plan"] == (
        "Development and implementation of a quantified plan setting out a "
        "decarbonisation strategy for scope 1, 2, and material scope 3."
    )
    assert NZIF_CRITERIA["emissions_performance"] == (
        "Current and forecast emissions performance (scope 1, 2 and "
        "material scope 3) relative to a net zero benchmark/pathway or an "
        "asset’s science-based target. An aligned asset would need to see "
        "emissions decline consistent with targets set to converge an "
        "asset with a net zero pathway."
    )


# ---------------------------------------------------------------------------
# NZIF_CRITERION_TIERS key-parity lock
# ---------------------------------------------------------------------------


def test_nzif_criterion_tiers_keys_match_nzif_criteria_keys_exactly():
    """
    NZIF_CRITERION_TIERS and NZIF_CRITERIA are two views of the same primary
    source table — they must cover exactly the same set of criteria. This test
    ensures they can never silently drift apart if one is edited without the
    other.
    """
    assert set(NZIF_CRITERION_TIERS.keys()) == set(NZIF_CRITERIA.keys())


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

# A realistic document excerpt — same text used as TSMC_DOCUMENT elsewhere,
# long enough to give quote_match something real to work with.
DOCUMENT = (
    "TSMC announced it is moving its target for 100 percent renewable energy "
    "consumption for all global operations forward to 2040 from 2050, "
    "accelerating its RE100 commitment by a full decade. The company publishes "
    "annual Scope 1 and Scope 2 GHG emissions data in accordance with the GHG "
    "Protocol Corporate Standard. TSMC's decarbonisation roadmap includes "
    "interim milestones for 2025 and 2030 tied to renewable energy procurement "
    "and energy efficiency programmes across its fabs."
)

# A verbatim excerpt that actually appears in DOCUMENT and is long enough to
# be verified as "unique" by quote_match.
GOOD_EXCERPT = (
    "moving its target for 100 percent renewable energy consumption for all "
    "global operations forward to 2040 from 2050"
)

CRITERION_NAME = "ambition"
CRITERION_TEXT = NZIF_CRITERIA["ambition"]


# ---------------------------------------------------------------------------
# Successful verification
# ---------------------------------------------------------------------------


def test_found_true_with_real_excerpt_returns_excerpt_verified():
    """
    LLM returns found=True with an excerpt that quote_match confirms as
    "unique" in the document → overall status is "excerpt_verified".
    """

    def good_llm(document, criterion_text, feedback):
        return {"found": True, "excerpt": GOOD_EXCERPT}

    result = find_criterion_evidence(
        DOCUMENT, CRITERION_NAME, CRITERION_TEXT, llm_fn=good_llm
    )
    assert result["status"] == "excerpt_verified"
    assert result["excerpt"] == GOOD_EXCERPT
    assert result["top_score"] is not None
    assert result["attempts"] == 1
    assert result["last_attempt_status"] == "excerpt_verified"


def test_verified_excerpt_top_score_is_high():
    """The verified excerpt's quote_match score should reflect a strong match."""

    def good_llm(document, criterion_text, feedback):
        return {"found": True, "excerpt": GOOD_EXCERPT}

    result = find_criterion_evidence(
        DOCUMENT, CRITERION_NAME, CRITERION_TEXT, llm_fn=good_llm
    )
    assert result["top_score"] is not None
    assert result["top_score"] > 80.0


# ---------------------------------------------------------------------------
# criterion_not_found handling
# ---------------------------------------------------------------------------


def test_found_false_returns_criterion_not_found_status():
    """
    LLM returns found=False → attempt fails as "criterion_not_found", NOT a
    terminal failure on first occurrence. The retry loop continues.
    """
    calls = [0]

    def always_not_found(document, criterion_text, feedback):
        calls[0] += 1
        return {"found": False, "excerpt": None}

    result = find_criterion_evidence(
        DOCUMENT, CRITERION_NAME, CRITERION_TEXT, llm_fn=always_not_found
    )
    assert result["status"] == "not_found_after_retries"
    assert result["last_attempt_status"] == "criterion_not_found"


def test_criterion_not_found_feedback_is_passed_to_next_attempt():
    """
    After a found=False attempt, the next call receives the not-found
    feedback string so the model knows to look more carefully.
    """
    feedbacks_received = []

    def capturing_llm(document, criterion_text, feedback):
        feedbacks_received.append(feedback)
        if len(feedbacks_received) == 1:
            return {"found": False, "excerpt": None}
        return {"found": True, "excerpt": GOOD_EXCERPT}

    result = find_criterion_evidence(
        DOCUMENT, CRITERION_NAME, CRITERION_TEXT, llm_fn=capturing_llm
    )
    assert result["status"] == "excerpt_verified"
    assert feedbacks_received[0] is None  # first call: no prior feedback
    assert feedbacks_received[1] == _CRITERION_NOT_FOUND_FEEDBACK


def test_two_consecutive_criterion_not_found_triggers_early_stop():
    """
    Two identical criterion_not_found attempts → same stage_reached, same
    status → no_meaningful_progress fires → early stop at attempt 2, not 3.
    """

    def always_not_found(document, criterion_text, feedback):
        return {"found": False, "excerpt": None}

    result = find_criterion_evidence(
        DOCUMENT,
        CRITERION_NAME,
        CRITERION_TEXT,
        llm_fn=always_not_found,
        max_attempts=3,
    )
    assert result["status"] == "not_found_after_retries"
    assert result["attempts"] == 2  # early stop, not hard cap of 3


# ---------------------------------------------------------------------------
# quote_match rejection
# ---------------------------------------------------------------------------


def test_found_true_with_excerpt_not_in_document_returns_quote_match_status():
    """
    LLM returns found=True but the excerpt doesn't actually appear in the
    document. quote_match rejects it; the specific quote_match failure status
    is surfaced, not a generic failure.
    """

    def hallucinated_excerpt_llm(document, criterion_text, feedback):
        return {
            "found": True,
            "excerpt": "TSMC has committed to net zero emissions by 2030 from all facilities worldwide",
        }

    result = find_criterion_evidence(
        DOCUMENT, CRITERION_NAME, CRITERION_TEXT, llm_fn=hallucinated_excerpt_llm
    )
    assert result["status"] == "not_found_after_retries"
    # The last attempt status should be a specific quote_match failure, not generic
    assert result["last_attempt_status"] in (
        "no_match",
        "ambiguous",
        "numeric_mismatch",
        "quote_too_short",
    )


def test_found_true_with_too_short_excerpt_returns_quote_too_short():
    """
    A very short excerpt (even if it appears in the document) is rejected by
    quote_match as "quote_too_short" — not silently accepted.
    """

    def short_excerpt_llm(document, criterion_text, feedback):
        return {"found": True, "excerpt": "2040"}

    result = find_criterion_evidence(
        DOCUMENT, CRITERION_NAME, CRITERION_TEXT, llm_fn=short_excerpt_llm
    )
    assert result["status"] == "not_found_after_retries"
    assert result["last_attempt_status"] == "quote_too_short"


# ---------------------------------------------------------------------------
# Malformed LLM response
# ---------------------------------------------------------------------------


def test_llm_raising_exception_does_not_propagate():
    """
    A mocked llm_fn that raises must NOT crash extract_claim_evidence.
    It is caught and recorded as "malformed_llm_response".
    """

    def exploding_llm(document, criterion_text, feedback):
        raise ValueError("model returned invalid JSON")

    result = find_criterion_evidence(
        DOCUMENT, CRITERION_NAME, CRITERION_TEXT, llm_fn=exploding_llm
    )
    assert result["status"] == "not_found_after_retries"
    assert result["last_attempt_status"] == "malformed_llm_response"


def test_llm_missing_found_field_does_not_propagate():
    """
    A response dict missing the 'found' key must be caught as
    malformed_llm_response, not a KeyError crash.
    """

    def missing_field_llm(document, criterion_text, feedback):
        return {"excerpt": "some text but no found field"}

    result = find_criterion_evidence(
        DOCUMENT, CRITERION_NAME, CRITERION_TEXT, llm_fn=missing_field_llm
    )
    assert result["status"] == "not_found_after_retries"
    assert result["last_attempt_status"] == "malformed_llm_response"


def test_two_consecutive_malformed_responses_trigger_early_stop():
    """
    Two malformed_llm_response attempts → same stage_reached + status → early stop.
    """

    def always_broken_llm(document, criterion_text, feedback):
        raise ValueError("always broken")

    result = find_criterion_evidence(
        DOCUMENT,
        CRITERION_NAME,
        CRITERION_TEXT,
        llm_fn=always_broken_llm,
        max_attempts=3,
    )
    assert result["attempts"] == 2
    assert result["last_attempt_status"] == "malformed_llm_response"


def test_malformed_llm_feedback_is_passed_to_next_attempt():
    """
    After a malformed response, the next call receives the specific
    malformed-response feedback so the model knows to fix its output format.
    """
    feedbacks = []

    def recovering_llm(document, criterion_text, feedback):
        feedbacks.append(feedback)
        if len(feedbacks) == 1:
            raise ValueError("first call broken")
        return {"found": True, "excerpt": GOOD_EXCERPT}

    result = find_criterion_evidence(
        DOCUMENT, CRITERION_NAME, CRITERION_TEXT, llm_fn=recovering_llm
    )
    assert result["status"] == "excerpt_verified"
    assert feedbacks[1] == _MALFORMED_LLM_RESPONSE_FEEDBACK


# ---------------------------------------------------------------------------
# Stage-based no-progress / early-stop
# ---------------------------------------------------------------------------


def test_different_stage_reached_counts_as_progress():
    """
    malformed_llm_response → criterion_not_found is a stage change → progress
    → no early stop fires. The loop continues.
    """
    calls = [0]

    def stage_changing_llm(document, criterion_text, feedback):
        calls[0] += 1
        if calls[0] == 1:
            raise ValueError("broken on first call")
        return {"found": False, "excerpt": None}

    result = find_criterion_evidence(
        DOCUMENT,
        CRITERION_NAME,
        CRITERION_TEXT,
        llm_fn=stage_changing_llm,
        max_attempts=2,
    )
    # attempt 1: malformed_llm_response; attempt 2: criterion_not_found
    # Different stages → no early stop → loop ran to max_attempts=2
    assert result["attempts"] == 2
    assert result["last_attempt_status"] == "criterion_not_found"


def test_malformed_then_verified_counts_as_progress():
    """
    malformed_llm_response (attempt 1) → excerpt_verified (attempt 2):
    different stages → progress → no early stop between attempts 1 and 2.
    """
    calls = [0]

    def recovering_llm(document, criterion_text, feedback):
        calls[0] += 1
        if calls[0] == 1:
            raise ValueError("broken on first call")
        return {"found": True, "excerpt": GOOD_EXCERPT}

    result = find_criterion_evidence(
        DOCUMENT, CRITERION_NAME, CRITERION_TEXT, llm_fn=recovering_llm
    )
    assert result["status"] == "excerpt_verified"
    assert result["attempts"] == 2


# ---------------------------------------------------------------------------
# Criteria independence
# ---------------------------------------------------------------------------


def test_two_different_criteria_run_independently():
    """
    A failure finding evidence for "ambition" does not affect or get
    conflated with a successful search for "disclosure". Each criterion is
    checked by a separate call and returns an independent result.
    """

    def disclosure_llm(document, criterion_text, feedback):
        # Returns a real disclosure excerpt regardless of which criterion is passed
        return {
            "found": True,
            "excerpt": (
                "publishes annual Scope 1 and Scope 2 GHG emissions data "
                "in accordance with the GHG Protocol Corporate Standard"
            ),
        }

    def not_found_llm(document, criterion_text, feedback):
        return {"found": False, "excerpt": None}

    ambition_result = find_criterion_evidence(
        DOCUMENT,
        "ambition",
        NZIF_CRITERIA["ambition"],
        llm_fn=not_found_llm,
    )
    disclosure_result = find_criterion_evidence(
        DOCUMENT,
        "disclosure",
        NZIF_CRITERIA["disclosure"],
        llm_fn=disclosure_llm,
    )

    assert ambition_result["status"] == "not_found_after_retries"
    assert disclosure_result["status"] == "excerpt_verified"
    # Failure on one criterion is completely isolated from the other
    assert ambition_result["last_attempt_status"] == "criterion_not_found"
    assert disclosure_result["last_attempt_status"] == "excerpt_verified"
