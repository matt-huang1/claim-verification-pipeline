"""
bucket_triage.py

Bucket C entry point: given a claim's text alone (no document, no search,
no fetch), decide whether the claim belongs to:

  - "bucket_a"   — a single authoritative source exists in principle
                   (the underlying category is precisely bounded)
  - "bucket_c"   — no single authoritative source exists
                   (the underlying category is definitionally contested)
  - "ambiguous"  — the model cannot confidently classify it either way;
                   this is a stable, honest finding about the claim itself,
                   not a transient miss, and is never retried

WHY THIS IS NOT A KEYWORD/REGEX CHECK:

"TSMC is the world's largest pure-play foundry by revenue" contains "largest",
a comparative/ranking word that might seem to signal Bucket C, but is actually
Bucket A: "pure-play foundry" is a precisely bounded category with one real,
knowable answer from revenue figures.

Conversely, "TSMC has roughly 60% of the foundry market" has no comparative
keyword at all in some phrasings, yet is genuinely Bucket C: "the foundry
market" has no single agreed boundary (does it include IDMs' in-house fab
capacity or not?).

The real distinguishing test is whether the claim's underlying category is
precisely bounded (Bucket A) or definitionally contested (Bucket C) — not
anything detectable from surface wording, which can mislead in either
direction. This requires real judgment, not pattern matching.

WHY "AMBIGUOUS" IS NEVER RETRIED:

Genuine classification uncertainty here is a stable, honest finding about the
claim itself — the same character as quote_match.py's "ambiguous" status,
which is presented as-is, not looped on. A claim that gets "ambiguous" is
handed to a human exactly as that: a real, final finding that the system could
not confidently route it, not a failure to fix by trying again.
"""

import json
import os

from dotenv import load_dotenv

load_dotenv()

MODEL = os.getenv("OPENAI_MODEL", "gpt-5-nano")

_VALID_CLASSIFICATIONS = {"bucket_a", "bucket_c", "ambiguous"}

_SYSTEM_PROMPT = """\
You classify factual claims into exactly one of three routing categories:

  bucket_a — a single authoritative source exists in principle. The claim's
             underlying category is precisely bounded, so there is one real,
             knowable answer (e.g. from a company filing, a regulatory
             disclosure, or a definitional standard that everyone uses).

  bucket_c — no single authoritative source exists in principle. The claim's
             underlying category is definitionally contested: reasonable
             analysts drawing on different but valid methodologies or scope
             definitions would arrive at different numbers, so no one source
             is uniquely authoritative.

  ambiguous — you cannot confidently classify this claim as bucket_a or
              bucket_c. Use this only when genuine uncertainty about the
              category boundary prevents confident routing; do not use it as
              a fallback for claims that are merely unusual.

THE REAL DISTINGUISHING TEST is not surface wording. It is whether the
claim's underlying category is precisely bounded or definitionally contested.
Surface wording is an unreliable signal and can mislead in either direction —
here are two worked counterexamples that prove this:

  MISLEADING TOWARD bucket_c (actually bucket_a):
    "TSMC is the world's largest pure-play foundry by revenue"
    This contains "largest", a comparative/ranking word. But "pure-play
    foundry" is a precisely bounded category — there is one real, knowable
    answer from revenue figures. This is bucket_a.

  MISLEADING TOWARD bucket_a (actually bucket_c):
    "TSMC has roughly 60% of the foundry market"
    No comparative keyword. But "the foundry market" has no single agreed
    boundary — does it include IDMs' in-house fab capacity or not? Different
    analysts drawing on different valid scope definitions produce different
    figures. This is bucket_c.

Apply this same reasoning to the claim you are given. Respond with ONLY a
JSON object with exactly two fields:
  "classification": one of "bucket_a", "bucket_c", or "ambiguous"
  "reasoning": a short explanation of why you chose this classification
               (required on every outcome, including bucket_a and bucket_c)
"""


def _default_llm_call(claim_text: str) -> dict:
    """The real LLM call. Tests inject a fake via the `llm_fn` parameter."""
    from openai import OpenAI

    client = OpenAI()
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"Claim: {claim_text}"},
        ],
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


def triage_claim(claim_text: str, llm_fn=None) -> dict:
    """
    Classify a claim into bucket_a, bucket_c, ambiguous, or malformed_llm_response.

    `llm_fn`, if provided, replaces the real OpenAI call. Signature:
        (claim_text: str) -> dict
    returning {"classification": str, "reasoning": str}.

    Returns:
        {
            "classification": str,  # "bucket_a" | "bucket_c" | "ambiguous"
                                    # | "malformed_llm_response"
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
