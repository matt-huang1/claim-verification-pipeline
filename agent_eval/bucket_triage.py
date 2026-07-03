"""
bucket_triage.py

Claim triage: given a claim's text alone (no document, no search, no
fetch), decide whether the claim belongs to:

  - "bucket_a"   — a single authoritative source exists in principle
                   (the underlying category is precisely bounded)
  - "bucket_c"   — no single authoritative source exists
                   (the underlying category is definitionally contested)
  - "bucket_d"   — the claim is future-facing or counterfactual, so no
                   fact-check is possible even in principle
  - "ambiguous"  — the model cannot confidently classify it;
                   this is a stable, honest finding about the claim itself,
                   not a transient miss, and is never retried

Bucket B is never a triage output — identifying which external framework
applies to a company is a human decision, supplied as an explicit
bucket="B" to run_pipeline.py, not something derivable from claim text.

WHY THIS IS NOT A KEYWORD/REGEX CHECK:

"TSMC's revenue was $69.3 billion in FY2023" looks like it might be Bucket C
(a specific number invites definitional scrutiny), but is actually Bucket A:
there is one real, knowable answer from TSMC's own published financial
statements with no definitional contest possible.

Conversely, "TSMC has roughly 60% of the foundry market" has no comparative
keyword at all, yet is genuinely Bucket C: "the foundry market" has no single
agreed boundary (does it include IDMs' in-house fab capacity or not?).

The real distinguishing test is whether the claim's underlying category is
precisely bounded (Bucket A), definitionally contested (Bucket C), or
uncheckable in principle (Bucket D) — not anything detectable from surface
wording, which can mislead in every direction. This requires real judgment,
not pattern matching.

WHY "AMBIGUOUS" IS NEVER RETRIED:

Genuine classification uncertainty here is a stable, honest finding about the
claim itself — the same character as quote_match.py's "ambiguous" status,
which is presented as-is, not looped on. A claim that gets "ambiguous" is
handed to a human exactly as that: a real, final finding that the system could
not confidently route it, not a failure to fix by trying again.
"""

import json

from agent_eval.llm_client import default_complete_json

_VALID_CLASSIFICATIONS = {"bucket_a", "bucket_c", "bucket_d", "ambiguous"}

_SYSTEM_PROMPT = """\
You classify factual claims into exactly one of four routing categories:

  bucket_a — a single authoritative source exists in principle. The claim's
             underlying category is precisely bounded, so there is one real,
             knowable answer (e.g. from a company filing, a regulatory
             disclosure, or a definitional standard that everyone uses).
             This includes claims about commitments a company has publicly
             made — the commitment is a present-tense checkable fact, even
             if the target date is in the future.

  bucket_c — no single authoritative source exists in principle. The claim's
             underlying category is definitionally contested: reasonable
             analysts drawing on different but valid methodologies or scope
             definitions would arrive at different numbers, so no one source
             is uniquely authoritative.

  bucket_d — the claim is future-facing or counterfactual, and no fact-check
             is possible even in principle. This covers claims about what
             would happen in a hypothetical world, or projections whose truth
             cannot be established from any present source. The question is
             not whether the claim is true, but whether its reasoning is
             explicit.

  ambiguous — you cannot confidently classify this claim as bucket_a,
              bucket_c, or bucket_d. Use this only when genuine uncertainty
              about the category boundary prevents confident routing; do not
              use it as a fallback for claims that are merely unusual.

THE REAL DISTINGUISHING TEST is not surface wording. It is whether the
claim's underlying category is precisely bounded (bucket_a), definitionally
contested (bucket_c), or uncheckable in principle (bucket_d). Surface
wording is an unreliable signal and can mislead — here are three worked
counterexamples that prove this:

  MISLEADING TOWARD bucket_c (actually bucket_a):
    "TSMC's revenue was $69.3 billion in FY2023."
    This is a specific historical financial figure. There is one real,
    knowable answer from TSMC's own published financial statements — no
    definitional contest, no analyst disagreement. This is bucket_a.

  MISLEADING TOWARD bucket_a (actually bucket_c):
    "TSMC has roughly 60% of the foundry market"
    No comparative keyword. But "the foundry market" has no single agreed
    boundary — does it include IDMs' in-house fab capacity or not? Different
    analysts drawing on different valid scope definitions produce different
    figures. This is bucket_c.

  MISLEADING TOWARD bucket_d (actually bucket_a):
    "TSMC committed to achieving RE100 by 2040."
    This mentions a future target date. But the commitment itself is a
    present-tense verifiable fact — TSMC either made this commitment in a
    real press release or did not. There is one authoritative source. This
    is bucket_a, not bucket_d.

  GENUINELY bucket_d:
    "Without TSMC, the climate transition would be set back by a decade."
    No source can verify this — it describes a counterfactual world that
    does not exist. The question is not whether it is true, but whether
    the reasoning behind it is explicitly stated. This is bucket_d.

Apply this same reasoning to the claim you are given. Respond with ONLY a
JSON object with exactly two fields:
  "classification": one of "bucket_a", "bucket_c", "bucket_d", or "ambiguous"
  "reasoning": a short explanation of why you chose this classification
               (required on every outcome)
"""


def _default_llm_call(claim_text: str) -> dict:
    """The real LLM call. Tests inject a fake via the `llm_fn` parameter."""
    return json.loads(default_complete_json(_SYSTEM_PROMPT, f"Claim: {claim_text}"))


def triage_claim(claim_text: str, llm_fn=None) -> dict:
    """
    Classify a claim into bucket_a, bucket_c, bucket_d, ambiguous, or
    malformed_llm_response.

    `llm_fn`, if provided, replaces the real OpenAI call. Signature:
        (claim_text: str) -> dict
    returning {"classification": str, "reasoning": str}.

    Returns:
        {
            "classification": str,  # "bucket_a" | "bucket_c" | "bucket_d"
                        # | "ambiguous" | "malformed_llm_response"
            "reasoning": str | None,  # model's reasoning, or None if malformed
        }

    No retry loop exists in this function. Classification uncertainty
    ("ambiguous") is a stable finding about the claim itself, not a transient
    miss. malformed_llm_response is reported honestly and left for the caller
    or a human to decide what to do.
    """
    llm_fn = llm_fn or _default_llm_call

    try:
        response = llm_fn(claim_text)
        classification = response["classification"]
        reasoning = response["reasoning"]
        if not isinstance(classification, str):
            raise ValueError("'classification' must be a string")
        if classification not in _VALID_CLASSIFICATIONS:
            raise ValueError(f"unknown classification: {classification!r}")
        if not isinstance(reasoning, str):
            raise ValueError("'reasoning' must be a string")
    except Exception:
        return {"classification": "malformed_llm_response", "reasoning": None}

    return {"classification": classification, "reasoning": reasoning}
