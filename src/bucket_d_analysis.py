"""
bucket_d_analysis.py

Bucket D analysis: given a future-facing or counterfactual claim text,
call the LLM once to surface a structured partial reading of the claim's
reasoning — what assumptions and causal steps are explicitly present, and
what is missing or unstated.

NO VERDICT IS REACHED HERE. This module collects what the LLM can see;
a human reviewer reads the structured output alongside the original claim
and decides whether the claim is honest enough to include in a report.

DESIGN DECISIONS:

WHY ONE LLM CALL PER analyze_assumptions INVOCATION:

Bucket D has no network fetch, no search, no URL selection. The entire
input to the model is the claim text itself. One call extracts both
assumptions and causal steps in a single structured response — splitting
into two calls would require the model to read the same claim twice and
would produce no additional signal.

WHY BOTH EMPTY LISTS ARE A VALID, NON-ERROR RESULT:

A claim so thinly written that the LLM cannot identify any explicit
assumption or causal step is a real, honest finding. The resulting
overall_status ("assumptions_not_stated") correctly reports what was
found. Empty lists are not the same as a malformed response — the
distinction is whether the response is structurally valid (parseable,
correctly shaped) versus whether it contains content.

WHY THE RETRY LOGIC IS MALFORMED-ONLY:

A well-formed response where every present_in_claim=False is a genuine
judgment outcome — the claim's text really does not state those things.
It is never retried. This mirrors reconciliation.py's "all unresolved"
path: a globally valid but informationally empty result is still final.
Retrying would only waste a call on a result that the model will likely
reproduce identically.

WHY "failed" RETURNS AssumptionsStatedEvidence WITH EMPTY LISTS:

The caller always gets a typed AssumptionsStatedEvidence, never None.
This is the same principle as reconcile_sources: a processing failure
is surfaced in the notes field and the result still has a valid shape,
so the caller's code path is identical regardless of outcome. Checking
for None at the call site would force every caller to handle a shape
the type system says will never exist.
"""

import json
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

from log_utils import append_log_entry
from tag_schema import AssumptionItem, AssumptionsStatedEvidence, CausalStep

load_dotenv()

MODEL = os.getenv("OPENAI_MODEL", "gpt-5-nano")

# Hard cap on LLM attempts for one analyze_assumptions call. A second
# attempt includes specific feedback about the first's defect; a third
# would add nothing not already communicated.
_MAX_ANALYSIS_ATTEMPTS = 2

# Named feedback constants for each defect type. Module-level so tests
# can import and assert the exact strings — same pattern as reconciliation.py.
_MALFORMED_JSON_FEEDBACK = (
    "your previous response could not be parsed as JSON - respond "
    "with ONLY a valid JSON object with exactly two fields: "
    "'assumptions' and 'causal_steps'"
)
_MISSING_FIELDS_FEEDBACK = (
    "your previous response was missing required fields - respond "
    "with a JSON object containing exactly 'assumptions' and "
    "'causal_steps', each a list of objects"
)
_INVALID_ITEM_FEEDBACK = (
    "your previous response contained invalid items - every item "
    "in 'assumptions' and 'causal_steps' must have a non-empty "
    "'text' string and a boolean 'present_in_claim' field "
    "(true or false, not a string or number)"
)

_SYSTEM_PROMPT = """\
You analyze a claim to surface its explicit assumptions and causal steps \
for a human reviewer. Your job is NOT to judge whether the claim is \
adequate, correct, or well-reasoned — that is the human reviewer's \
decision.

YOUR ROLE: Surface what is explicitly present in the claim text and flag \
what is missing or unstated. Never reach a verdict on whether the claim \
is adequate. Never invent assumptions or steps that are not at least \
implied by the claim text.

present_in_claim=true means the assumption or step is explicitly written \
in the claim text (or so directly implied that a careful reader would not \
miss it). present_in_claim=false means it appears to be missing, unstated, \
or requires an inferential leap not supported by the text.

WORKED EXAMPLE:

  Claim: "Without TSMC, the global climate transition would be set back \
by a decade because advanced chips are essential for clean energy technology."

  Correct output:
    assumptions: [
      {
        "text": "No other fab can substitute for TSMC's advanced chip \
manufacturing at scale",
        "present_in_claim": false
      },
      {
        "text": "Advanced chips are essential for clean energy technology",
        "present_in_claim": true
      }
    ]
    causal_steps: [
      {
        "text": "TSMC absence → no advanced chip supply at scale",
        "present_in_claim": false
      },
      {
        "text": "No advanced chips → clean energy technology deployment slows",
        "present_in_claim": true
      },
      {
        "text": "Deployment slowdown → decade-scale climate transition delay",
        "present_in_claim": false
      }
    ]

  Note: "Advanced chips are essential for clean energy technology" is \
present_in_claim=true because it is explicitly stated in the claim text. \
"No other fab can substitute" is present_in_claim=false because the claim \
does not state this — it is an unstated premise the argument requires but \
does not make explicit.

Respond with ONLY a JSON object with exactly two fields:
  "assumptions": a list of objects, each with:
    - "text": a non-empty string (the assumption, as stated or paraphrased)
    - "present_in_claim": a boolean (true or false, never a string or number)
  "causal_steps": a list of objects, each with:
    - "text": a non-empty string (the step, e.g. "X → Y")
    - "present_in_claim": a boolean (true or false, never a string or number)
"""


def _default_llm_call(claim_text: str, feedback: str | None) -> dict:
    """Real LLM call. Tests inject a fake via llm_fn."""
    from openai import OpenAI

    client = OpenAI()

    user = f"Claim: {claim_text}"
    if feedback:
        user += (
            f"\n\nThe previous response was invalid because {feedback}. "
            "Please try again."
        )

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


def _validate_response(raw: dict) -> tuple[bool, str | None]:
    """
    Check a parsed LLM response for all structural defects.

    Returns (is_valid, feedback_constant_or_None). Checks are ordered
    so the most actionable feedback is returned first.
    """
    # Both fields must be present and must be lists
    if "assumptions" not in raw or "causal_steps" not in raw:
        return False, _MISSING_FIELDS_FEEDBACK
    if not isinstance(raw["assumptions"], list) or not isinstance(
        raw["causal_steps"], list
    ):
        return False, _MISSING_FIELDS_FEEDBACK

    # Every item in both lists must be structurally valid
    for item in raw["assumptions"] + raw["causal_steps"]:
        if not isinstance(item, dict):
            return False, _INVALID_ITEM_FEEDBACK
        if "text" not in item or "present_in_claim" not in item:
            return False, _INVALID_ITEM_FEEDBACK
        if not isinstance(item["text"], str) or not item["text"]:
            return False, _INVALID_ITEM_FEEDBACK
        # isinstance(val, bool) rejects "true", 1, None — int is a subclass
        # of bool in Python, so check bool first to reject plain integers
        if not isinstance(item["present_in_claim"], bool):
            return False, _INVALID_ITEM_FEEDBACK

    return True, None


def _build_evidence(raw: dict) -> AssumptionsStatedEvidence:
    """Build AssumptionsStatedEvidence from a validated response dict."""
    assumptions = [
        AssumptionItem(text=a["text"], present_in_claim=a["present_in_claim"])
        for a in raw["assumptions"]
    ]
    causal_steps = [
        CausalStep(text=s["text"], present_in_claim=s["present_in_claim"])
        for s in raw["causal_steps"]
    ]
    return AssumptionsStatedEvidence(
        assumptions=assumptions,
        causal_steps=causal_steps,
        notes="",
    )


def analyze_assumptions(
    claim_text: str,
    *,
    company_name: str,
    llm_fn=None,
    log_dir: str = "logs",
) -> AssumptionsStatedEvidence:
    """
    Surface a structured partial reading of `claim_text`'s reasoning.

    Calls the LLM once (with one retry on malformed response) and returns
    an AssumptionsStatedEvidence with the assumptions and causal steps
    the LLM can identify. Never returns None. A malformed-after-retries
    outcome returns an AssumptionsStatedEvidence with empty lists and a
    notes field describing the failure.

    `company_name` is a required keyword-only parameter, never inferred
    from claim_text. An incorrect company name at the call site is visible;
    a silently misparsed one would corrupt log entries without any failure
    signal — same principle as reconcile_sources.

    `llm_fn`, if provided, replaces the real OpenAI call. Signature:
        (claim_text: str, feedback: str | None) -> dict
    returning {"assumptions": [...], "causal_steps": [...]}.
    feedback is None on the first call.

    Appends exactly one structured log entry to log_dir/evaluation_log.jsonl
    after the result is fully built.
    """
    _llm_fn = llm_fn or _default_llm_call

    feedback: str | None = None
    result: AssumptionsStatedEvidence | None = None
    outcome = "failed"
    attempts_made = 0

    for attempt in range(1, _MAX_ANALYSIS_ATTEMPTS + 1):
        attempts_made = attempt
        try:
            raw = _llm_fn(claim_text, feedback)
            if not isinstance(raw, dict):
                raise ValueError("response must be a dict")
        except Exception:
            feedback = _MALFORMED_JSON_FEEDBACK
            if attempt == _MAX_ANALYSIS_ATTEMPTS:
                break
            continue

        is_valid, bad_feedback = _validate_response(raw)
        if is_valid:
            result = _build_evidence(raw)
            outcome = "analyzed"
            break

        feedback = bad_feedback
        if attempt == _MAX_ANALYSIS_ATTEMPTS:
            break

    if result is None:
        result = AssumptionsStatedEvidence(
            assumptions=[],
            causal_steps=[],
            notes="Analysis failed after all attempts — malformed LLM response.",
        )

    append_log_entry(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "bucket": "D",
            "company_name": company_name,
            "claim_text": claim_text,
            "assumptions_found": len(result.assumptions),
            "causal_steps_found": len(result.causal_steps),
            "stated_assumptions_count": sum(
                1 for a in result.assumptions if a.present_in_claim
            ),
            "stated_steps_count": sum(
                1 for s in result.causal_steps if s.present_in_claim
            ),
            "attempts": attempts_made,
            "outcome": outcome,
        },
        log_dir,
    )
    return result
