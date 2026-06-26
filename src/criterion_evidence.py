"""
criterion_evidence.py

Bucket B evidence-gathering: given a document already in hand and one specific
NZIF 2.0 alignment criterion, find and verify a real excerpt from the document
that addresses that criterion.

SCOPE — this module does exactly one thing:

It does NOT find sources, fetch documents, or run web searches. That work is
already done by web_search.py, url_compare.py, and page_fetch.py, called by
whatever orchestrates Bucket B claims before this module runs. This module's
only job is: given document text already in hand, and one criterion, find and
verify the excerpt addressing it.

WHY CRITERIA TEXT IS HARDCODED, NOT SEARCHED FOR:

NZIF 2.0's alignment criteria table is a static primary-source document.
The criteria wording is known, fixed, and already used in the project
(see tag_schema.py's CriterionEvidence.criterion_text). Searching for or
re-deriving it on each run would introduce a model-mediated restatement
of the criterion — exactly what criterion_text is designed to prevent by
requiring the real wording. The criteria are hardcoded in NZIF_CRITERIA
below, sourced from the IIGCC Net Zero Investment Framework 2.0 (2024).

DATED STARTING ASSUMPTION: the criteria text below was transcribed directly
from the primary source PDF (IIGCC, Net Zero Investment Framework 2.0,
https://www.iigcc.org/hubfs/NZIF%202.0%20Report%20PDF.pdf, "Criteria
underpinning alignment assessment" table) by the user on 2026-06-26.
The original version of this dict was found, on review, to not match the
primary source — it was an LLM reconstruction that invented Scope 1/2/3
detail under "ambition", included a "capital_allocation" criterion that
does not exist in the real table, and was missing "emissions_performance"
entirely. The current text is the corrected, user-transcribed version.
If IIGCC releases a new NZIF version, this hardcoded text needs manual
review and update. No automated staleness check is warranted: NZIF
revisions are rare (1.0 in 2021, 2.0 in 2024) and the cost of an
automated diff-and-flag system isn't justified at that frequency. The
same "documented starting assumption" pattern is used for
AMBIGUITY_GAP_THRESHOLD in quote_match.py and NO_PROGRESS_SCORE_DELTA
in extraction.py.

WHY "FOUND: FALSE" IS A RETRYABLE OUTCOME, NOT A TERMINAL ONE:

Giving the model an explicit `found: false` response option is a deliberate
design decision. A model without a legitimate "I don't know" option tends
to produce a plausible-but-wrong answer rather than admit it doesn't have
one — observed behavior in extraction.py's live runs. Accepting `false`
and retrying with corrective feedback (rather than immediately giving up)
is consistent with the same retry-with-feedback pattern used throughout
the extraction loop: one "not found" may mean the model read too quickly,
not that the criterion is genuinely absent.

WHY quote_match AND _build_feedback ARE REUSED DIRECTLY:

quote_match.match_quote() is the project's existing deterministic check
that a claimed excerpt actually appears in the source text. Reimplementing
it here — even a simplified version — would create two different
implementations of the same check, diverging over time. A model claiming
to have found something does not make it real; quote_match is what makes
it real, the same principle applied throughout Bucket A.

_build_feedback() in extraction.py already knows how to describe every
quote_match failure status in corrective terms the model can act on.
The failures here (ambiguous, no_match, numeric_mismatch, quote_too_short)
are identical in kind to Bucket A failures of the same names, so the
same feedback applies. Duplicating that logic here would also diverge.

RETRY STOPPING:

extraction.py's no_meaningful_progress() is reused directly. CriterionAttemptRecord
carries the same three fields it uses (stage_reached, status, top_score),
so no duck-typing ceremony is needed. This module's stages are named for
what they actually are here:
  "malformed_llm_response" — LLM response could not be parsed
  "criterion_not_found"    — model returned found=false
  "quote_match_failed"     — model returned found=true but quote_match rejected the excerpt
  "excerpt_verified"       — quote_match confirmed the excerpt; success
"""

import json
import os
from dataclasses import dataclass

from dotenv import load_dotenv

from extraction import NO_PROGRESS_SCORE_DELTA, _build_feedback, no_meaningful_progress
from quote_match import match_quote

load_dotenv()

MODEL = os.getenv("OPENAI_MODEL", "gpt-5-nano")

# NZIF 2.0 alignment criteria, transcribed verbatim from the primary source PDF:
# IIGCC, Net Zero Investment Framework 2.0, "Criteria underpinning alignment
# assessment" table. User-transcribed on 2026-06-26. See module docstring for
# the full "dated starting assumption" note.
#
# KNOWN SIMPLIFICATION — tier-mapping not yet represented:
# In the source table, each criterion is tied to specific alignment tiers
# (e.g. "ambition" applies to all four tiers; "emissions_performance" and
# "decarbonisation_plan" only apply to "Aligned to a net zero pathway" and
# "Achieving net zero"). This dict maps criterion_name -> criterion_text only.
# The tier-mapping is real information from the primary source that this
# structure does not yet capture. It is a known omission, not a silent one —
# the design decision of how ClaimTag/CriterionEvidence should represent
# "this criterion only matters for these specific tiers" should be made
# explicitly before being built.
NZIF_CRITERIA: dict[str, str] = {
    "ambition": (
        "A long term goal consistent with the global goal of achieving "
        "net zero by 2050."
    ),
    "targets": (
        "Short and medium term targets for scope 1, 2 and material scope 3 "
        "emissions in line with science-based ‘net zero’ pathway. These may "
        "be absolute, or intensity based: a) where available, a sectoral "
        "decarbonisation / carbon budget approach should be used; b) minimum "
        "for other assets is a global or regional average pathway."
    ),
    "disclosure": (
        "Disclosure of scope 1 and 2 emissions, and disclosure of material "
        "scope 3, in line with regulatory requirements where applicable or "
        "the PCAF Standard."
    ),
    "governance": (
        "Governance/management responsibility for targets and " "decarbonisation plan."
    ),
    "decarbonisation_plan": (
        "Development and implementation of a quantified plan setting out a "
        "decarbonisation strategy for scope 1, 2, and material scope 3."
    ),
    "emissions_performance": (
        "Current and forecast emissions performance (scope 1, 2 and "
        "material scope 3) relative to a net zero benchmark/pathway or an "
        "asset’s science-based target. An aligned asset would need to see "
        "emissions decline consistent with targets set to converge an "
        "asset with a net zero pathway."
    ),
}

# Feedback constants for pre-quote-match failures.
# Defined as module-level constants so tests can import and assert the exact
# strings — the same pattern as extraction.py's _NO_SEARCH_RESULTS_FEEDBACK.
_CRITERION_NOT_FOUND_FEEDBACK = (
    "no excerpt was found - read the document more thoroughly, "
    "the evidence may be phrased differently than you expect"
)
_MALFORMED_LLM_RESPONSE_FEEDBACK = (
    "your previous response could not be parsed - respond with "
    "ONLY a valid JSON object with exactly two fields: "
    "'found' (boolean) and 'excerpt' (string or null)"
)


@dataclass
class CriterionAttemptRecord:
    """
    One attempt to find a criterion excerpt, as fed to the stopping rule.

    Carries the three fields no_meaningful_progress() uses (stage_reached,
    status, top_score) so it can be passed directly to that function without
    duck-typing ceremony.

    stage_reached values:
      "malformed_llm_response" — LLM response could not be parsed
      "criterion_not_found"    — model returned found=false
      "quote_match_failed"     — excerpt proposed but quote_match rejected it
      "excerpt_verified"       — quote_match confirmed; success
    """

    attempt: int
    excerpt: str
    status: str
    top_score: float | None
    stage_reached: str


def _default_llm_call(document: str, criterion_text: str, feedback: str | None) -> dict:
    """
    The real LLM call. Presents the document and criterion to the model and
    asks it to find a verbatim supporting excerpt, or explicitly say none
    exists. Tests inject a fake via the `llm_fn` parameter.
    """
    from openai import OpenAI

    client = OpenAI()

    system = (
        "You help verify whether a company document satisfies a specific "
        "climate framework criterion. Given the document text and the "
        "criterion wording, find a verbatim excerpt from the document that "
        "directly addresses that criterion. "
        'Respond with ONLY a JSON object with exactly two fields: "found" '
        "(boolean: true if a relevant excerpt exists, false if not) and "
        '"excerpt" (the verbatim excerpt string if found is true, or null '
        "if found is false). The excerpt must be copied exactly from the "
        "document — do not paraphrase or summarise."
    )
    user = f"Criterion:\n{criterion_text}\n\nDocument:\n{document}"
    if feedback:
        user += (
            f"\n\nThe previous attempt failed because {feedback}. "
            "Try again, reading the document more carefully."
        )

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


def find_criterion_evidence(
    document: str,
    criterion_name: str,
    criterion_text: str,
    *,
    llm_fn=None,
    max_attempts: int = 3,
) -> dict:
    """
    Find and verify a real excerpt from `document` that addresses `criterion_text`.

    Each attempt:
      1. Calls `llm_fn(document, criterion_text, feedback)` for a proposal.
      2. Defensively parses the response — malformed JSON or a missing field
         yields "malformed_llm_response", never an uncaught exception.
      3. If found=False: records "criterion_not_found" and retries.
      4. If found=True: verifies the excerpt with quote_match.match_quote().
         A non-"unique" result is a named, retryable failure; "unique" is success.

    `llm_fn`, if provided, replaces the real OpenAI call. Signature:
        (document: str, criterion_text: str, feedback: str | None) -> dict
    returning {"found": bool, "excerpt": str | None}.

    Returns a dict:
        {
            "status": str,               # "excerpt_verified" or "not_found_after_retries"
            "excerpt": str | None,       # the verified excerpt on success, else None
            "top_score": float | None,   # quote_match score on success, else None
            "attempts": int,
            "last_attempt_status": str | None,
        }
    """
    llm_fn = llm_fn or _default_llm_call

    history: list[CriterionAttemptRecord] = []
    feedback: str | None = None

    for attempt in range(1, max_attempts + 1):
        # --- parse LLM response defensively ---
        try:
            response = llm_fn(document, criterion_text, feedback)
            found = response["found"]
            excerpt = response["excerpt"]
            if not isinstance(found, bool):
                raise ValueError("'found' must be a boolean")
            if found and not isinstance(excerpt, str):
                raise ValueError("'excerpt' must be a string when found is true")
        except Exception:
            record = CriterionAttemptRecord(
                attempt=attempt,
                excerpt="",
                status="malformed_llm_response",
                stage_reached="malformed_llm_response",
                top_score=None,
            )
            history.append(record)
            if len(history) >= 2 and no_meaningful_progress(
                history[-2], history[-1], NO_PROGRESS_SCORE_DELTA
            ):
                break
            feedback = _MALFORMED_LLM_RESPONSE_FEEDBACK
            continue

        # --- model said no excerpt exists ---
        if not found:
            record = CriterionAttemptRecord(
                attempt=attempt,
                excerpt="",
                status="criterion_not_found",
                stage_reached="criterion_not_found",
                top_score=None,
            )
            history.append(record)
            if len(history) >= 2 and no_meaningful_progress(
                history[-2], history[-1], NO_PROGRESS_SCORE_DELTA
            ):
                break
            feedback = _CRITERION_NOT_FOUND_FEEDBACK
            continue

        # --- model proposed an excerpt: verify it deterministically ---
        qm_result = match_quote(excerpt, document)
        if qm_result.status != "unique":
            top_score = qm_result.candidates[0].score if qm_result.candidates else None
            record = CriterionAttemptRecord(
                attempt=attempt,
                excerpt=excerpt,
                status=qm_result.status,
                stage_reached="quote_match_failed",
                top_score=top_score,
            )
            history.append(record)
            if len(history) >= 2 and no_meaningful_progress(
                history[-2], history[-1], NO_PROGRESS_SCORE_DELTA
            ):
                break
            feedback = _build_feedback(qm_result.status)
            continue

        # --- excerpt verified ---
        top_score = qm_result.candidates[0].score if qm_result.candidates else None
        return {
            "status": "excerpt_verified",
            "excerpt": excerpt,
            "top_score": top_score,
            "attempts": attempt,
            "last_attempt_status": "excerpt_verified",
        }

    return {
        "status": "not_found_after_retries",
        "excerpt": None,
        "top_score": None,
        "attempts": len(history),
        "last_attempt_status": history[-1].status if history else None,
    }
