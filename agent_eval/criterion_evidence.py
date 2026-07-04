"""Bucket B: find and verify one NZIF criterion excerpt in a given document.

Does exactly one thing: given document text already in hand and one NZIF 2.0
criterion, find and verify a verbatim excerpt addressing it. Search, URL
selection, and fetching happen upstream (bucket_b_pipeline.py).

Key decisions (full reasoning in adr/0010-criterion-evidence.md):

- NZIF_CRITERIA is hand-transcribed from the primary-source PDF, not searched
  for or model-derived — the original dict was an LLM reconstruction that
  invented and omitted real criteria, caught only by checking against the
  actual document. Dated transcription notes live on the constants below.
- `found: false` is a legitimate, retryable model response: without an
  explicit "not found" option, models produce plausible-but-wrong answers
  instead of admitting absence (observed live in extraction.py).
- quote_match and extraction.py's _build_feedback/no_meaningful_progress are
  reused directly rather than reimplemented, so the check and its corrective
  feedback cannot diverge between buckets.
"""

import json
from dataclasses import dataclass
from typing import Callable

from agent_eval.extraction import (
    NO_PROGRESS_SCORE_DELTA,
    _build_feedback,
    no_meaningful_progress,
)
from agent_eval.llm_client import default_complete_json
from agent_eval.quote_match import match_quote

# NZIF 2.0 alignment criteria, transcribed verbatim from the primary source PDF
# (IIGCC, Net Zero Investment Framework 2.0, "Criteria underpinning alignment
# assessment" table; hand-transcribed 2026-06-26). A golden-file test locks in
# the exact wording. If IIGCC releases a new NZIF version this needs manual
# review — revisions are rare (1.0 in 2021, 2.0 in 2024), so no automated
# staleness check is warranted. See adr/0010-criterion-evidence.md.
#
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

# Tier-applicability mapping, transcribed from the same IIGCC NZIF 2.0 primary
# source table as NZIF_CRITERIA (hand-transcribed 2026-06-26; both dicts need
# manual review together if IIGCC revises the framework). Maps criterion_name
# -> the alignment tier slugs the criterion applies to, so a reviewer never
# checks a criterion's evidence against a tier the framework doesn't require
# it for. Slugs are lowercased versions of the four real NZIF tier names
# ("Committed to aligning" -> "committed_to_aligning", etc.).
#
# Verified but not yet consumed by the runtime pipeline — the intended
# consumer is the human-facing review layer (see ROADMAP.md and adr/0010).
NZIF_CRITERION_TIERS: dict[str, list[str]] = {
    "ambition": [
        "committed_to_aligning",
        "aligning_to_a_net_zero_pathway",
        "aligned_to_a_net_zero_pathway",
        "achieving_net_zero",
    ],
    "governance": [
        "aligning_to_a_net_zero_pathway",
        "aligned_to_a_net_zero_pathway",
        "achieving_net_zero",
    ],
    "disclosure": [
        "aligning_to_a_net_zero_pathway",
        "aligned_to_a_net_zero_pathway",
        "achieving_net_zero",
    ],
    "targets": [
        "aligning_to_a_net_zero_pathway",
        "aligned_to_a_net_zero_pathway",
        "achieving_net_zero",
    ],
    "decarbonisation_plan": [
        "aligned_to_a_net_zero_pathway",
        "achieving_net_zero",
    ],
    "emissions_performance": [
        "aligned_to_a_net_zero_pathway",
        "achieving_net_zero",
    ],
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

    return json.loads(default_complete_json(system, user))


def find_criterion_evidence(
    document: str,
    criterion_name: str,
    criterion_text: str,
    *,
    llm_fn: Callable[[str, str, str | None], dict] | None = None,
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
