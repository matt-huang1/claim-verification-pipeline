"""
extraction.py

The AI extraction layer - the ONLY place in this project that calls a real
LLM. It asks a model to select the best candidate source URL from real web
search results and propose a supporting quote, then runs that proposal through
the existing, fully-deterministic pipeline (pipeline.verify_bucket_a_claim)
to check whether the self-report actually holds up.

Everything downstream of this file - domain_check, quote_match, tag_schema,
pipeline - stays exactly as built: no model, no randomness, fully testable
with no API key. This module is the single boundary where non-determinism
enters, and it is deliberately thin.

INDEPENDENT GROUND TRUTH - why the LLM does not see the document:

The model is given the claim text and real search-result candidates (URLs
+ snippets). The source `document` and the legitimacy `allowlist` are
supplied by the CALLER, not the model, and the proposal is checked against
them. This is the whole point: if the document or allowlist came from the
model, a hallucinated source would validate itself - exactly the
non-discriminating-verification failure this project exists to prevent. The
model's selection is only ever checked against ground truth it did not get
to choose.

(Note: this is why the public function takes `document` and `allowlist` in
addition to `claim_text`, rather than `claim_text` alone - verification is
meaningless without independent ground truth to verify against.)

SEARCH LAYER - why the model receives real URLs, not a blank prompt:

An earlier design asked the model to propose a source URL purely from its
training data. A live run showed this reliably produces plausible-but-
nonexistent URLs (URLs that look real but return 404 or belong to unrelated
content). Web search was added as the fix: before each LLM call, the
pipeline runs a Brave Search query against the claim text and passes the
real, verified-to-exist candidate URLs to the model. The model then selects
the best candidate from those URLs rather than generating one from memory.

If search returns no results, the attempt fails immediately with status
"no_search_results" and the LLM is NOT called. This is a firm, permanent
rule - not a soft default that can be revisited per-attempt. Falling back to
model memory whenever search is empty would create a standing escape hatch
that defeats the reason search was added: a model that knows the fallback
exists will learn that unverified URLs are always available as a backstop.
The "no_search_results" failure counts toward both the hard cap and the
no-progress early-stop rule - an empty-search-results loop is just as much
"no progress" as a repeated identical quote-match failure. See web_search.py
for the choice of Brave Search over OpenAI's bundled search options.

PRE-CHECK GATE - cost control before any API call:

Before calling the LLM or running a search, a cheap deterministic check
rejects claims that are too vague to verify (no numeric token, no
exclusivity/ranking word). This gate is DELIBERATELY INCOMPLETE: the
exclusivity word list is short and known not to be exhaustive. Some
genuinely vague claims will pass the gate and only get caught later when
quote_match finds nothing - that is an accepted tradeoff, not a bug. The
gate's only job is to cheaply catch the clearest unverifiable claims before
spending any API budget on them.

RETRY STOPPING - why "no progress", not a backoff delay:

Retries stop on a hard cap of 3 attempts, OR early if two consecutive
attempts produce the same status without the quote-match score meaningfully
improving. The failure mode being managed here is WRONG CONTENT (a
hallucinated quote, a bad URL), not an overloaded or rate-limited API - so
exponential backoff does not apply. Waiting longer does not make a
non-existent source exist. Repeated retries that aren't measurably moving
toward "verified" are a signal that the claim may not be backed by a
findable source at all, and continuing just burns API cost.

STRUCTURED LOG - so a human can audit failures, not just trust the verdict:

Every attempt (not only the final outcome) is appended to a JSON-lines log
in logs/. The purpose is to let a human review failures later and spot
patterns (a certain kind of claim failing the same way), and to
independently check whether a status was actually correct rather than
trusting the system's own verdict about its own failure - the same "don't
trust a confident result without the evidence to check it" principle the
rest of this project is built on. Log entries reuse the exact status
vocabulary tag_schema.py already defines; "no_search_results" is added here
for the search-failure case that has no tag_schema equivalent.
"""

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from dotenv import load_dotenv

from pipeline import verify_bucket_a_claim
from web_search import search_for_source

load_dotenv()

# Default model: the cheapest capable nano tier (simple extraction, not a
# reasoning task). Overridable via OPENAI_MODEL so the exact current-cheapest
# model can be swapped in without a code change as OpenAI's lineup shifts.
MODEL = os.getenv("OPENAI_MODEL", "gpt-5-nano")

# Hard ceiling on LLM calls per claim, regardless of anything else.
MAX_ATTEMPTS = 3

# Two consecutive same-status attempts whose top quote-match score improves
# by less than this many points count as "no meaningful progress" and stop
# the loop early. This threshold is a STARTING ASSUMPTION, not derived from
# data - documented as such, like AMBIGUITY_GAP_THRESHOLD in quote_match.py.
# Revisit against real logged runs once there are some.
NO_PROGRESS_SCORE_DELTA = 5.0

# Numeric-token pattern, mirrored from quote_match._extract_numeric_tokens
# (kept as a local copy so this module does not depend on a private symbol
# in another module). Years, percentages, decimals, plain integers.
_NUMERIC_TOKEN_PATTERN = r"\d+\.\d+%?|\d+%?"

# Deliberately narrow, known-incomplete list of exclusivity/ranking words.
# See module docstring, "PRE-CHECK GATE".
_EXCLUSIVITY_WORDS = ["first", "only", "world's", "largest", "smallest"]
_EXCLUSIVITY_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in _EXCLUSIVITY_WORDS) + r")\b"
)

# Feedback message used when a search attempt finds no results. Defined as a
# constant so the exact string can be asserted in tests.
_NO_SEARCH_RESULTS_FEEDBACK = (
    "no search results were found for this claim - this may indicate "
    "the claim is too obscure or not well-formed"
)


@dataclass
class AttemptRecord:
    """One extraction attempt, as logged and as fed to the stopping rule."""

    attempt: int
    url: str
    quote: str
    # Reuses tag_schema's overall_status vocabulary for verified/failed
    # verification attempts. "no_search_results" is specific to this module:
    # it represents an attempt where search returned nothing and the LLM was
    # not called at all, so there was no ClaimTag to draw a status from.
    status: str
    top_score: float | None
    timestamp: str


def _has_numeric_token(text: str) -> bool:
    return bool(re.search(_NUMERIC_TOKEN_PATTERN, text))


def is_verifiable_claim(claim_text: str) -> bool:
    """
    Cheap, deterministic pre-check (no API cost). True if the claim has at
    least one numeric token OR one exclusivity/ranking word. Deliberately
    incomplete - see module docstring.
    """
    if _has_numeric_token(claim_text):
        return True
    return bool(_EXCLUSIVITY_PATTERN.search(claim_text.lower()))


def no_meaningful_progress(
    previous: AttemptRecord,
    current: AttemptRecord,
    threshold: float = NO_PROGRESS_SCORE_DELTA,
) -> bool:
    """
    True if `current` did not meaningfully improve on `previous`: same
    status, and the top score rose by less than `threshold`. A None score
    is treated as 0.0 for this comparison. Different statuses always count
    as progress (the system is at least exploring a different failure),
    so this returns False in that case.
    """
    if previous.status != current.status:
        return False
    prev_score = previous.top_score if previous.top_score is not None else 0.0
    curr_score = current.top_score if current.top_score is not None else 0.0
    return (curr_score - prev_score) < threshold


def _build_feedback(status: str) -> str:
    """
    Turn a specific failure status into corrective guidance for the next
    LLM call, so a retry is informed rather than a blind re-roll.
    """
    if status == "numeric_mismatch":
        return (
            "the quote you provided did not contain the specific number "
            "(year/percentage/figure) that appears in the claim - try again "
            "with a quote that includes the exact number"
        )
    if status == "source_illegitimate":
        return (
            "the URL was not an accepted official source for this entity - "
            "try a different source on the entity's own domain"
        )
    if status == "ambiguous":
        return (
            "the quote matched several places in the source equally well - "
            "provide a longer, more specific quote"
        )
    if status == "no_match":
        return (
            "the quote could not be found in the source document - provide "
            "an exact, verbatim quote that actually appears in the source"
        )
    if status == "quote_too_short":
        return "the quote was too short to verify - provide a longer exact quote"
    return "verification failed - provide a different source URL and exact quote"


def _default_llm_call(
    claim_text: str, feedback: str | None, search_results: list[dict]
) -> dict:
    """
    The real LLM call. Presents real search candidates to the model and asks
    it to select the best matching URL and propose a verbatim supporting quote.
    This is the only function here that touches the OpenAI API; tests inject
    a fake in its place via the `llm_fn` parameter.
    """
    from openai import OpenAI

    client = OpenAI()

    candidates_text = "\n".join(
        f"{i + 1}. URL: {r['url']}\n   Title: {r['title']}\n   Snippet: {r['snippet']}"
        for i, r in enumerate(search_results)
    )

    system = (
        "You help verify factual claims. Given a claim and a list of candidate "
        "source URLs found by a web search, select the single best matching "
        "source and propose a verbatim supporting quote from it. Respond with "
        'ONLY a JSON object with exactly two string fields: "url" (the URL of '
        "your chosen source, selected from the provided candidates) and "
        '"quote" (a short, exact, verbatim quotation from that source that '
        "supports the claim, including any specific numbers or dates). Do not "
        "paraphrase the quote. Only use URLs from the provided candidates list."
    )
    user = (
        f"Claim: {claim_text}\n\nCandidate sources from web search:\n{candidates_text}"
    )
    if feedback:
        user += (
            f"\n\nThe previous attempt failed because {feedback}. "
            "Select a different source or propose a more precise quote."
        )

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
    )
    data = json.loads(response.choices[0].message.content)
    return {"url": data["url"], "quote": data["quote"]}


def _log_attempt(record: AttemptRecord, claim_text: str, log_dir: str) -> None:
    """Append one attempt to a JSON-lines log. Creates log_dir if needed."""
    os.makedirs(log_dir, exist_ok=True)
    entry = {
        "timestamp": record.timestamp,
        "claim_text": claim_text,
        "attempt": record.attempt,
        "url": record.url,
        "quote": record.quote,
        "status": record.status,
        "top_score": record.top_score,
    }
    path = os.path.join(log_dir, "extraction.jsonl")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def extract_claim_evidence(
    claim_text: str,
    document: str,
    allowlist: list[str],
    *,
    claim_id: str = "claim",
    llm_fn=None,
    search_fn=None,
    log_dir: str = "logs",
    max_attempts: int = MAX_ATTEMPTS,
) -> dict:
    """
    Extract and verify a source URL + quote for `claim_text`.

    `document` and `allowlist` are the independent ground truth the model's
    self-report is checked against (see module docstring) - they are NOT
    shown to the model.

    `llm_fn`, if provided, replaces the real OpenAI call. Signature:
        (claim_text: str, feedback: str | None, search_results: list[dict]) -> dict
    returning {"url": str, "quote": str}.

    `search_fn`, if provided, replaces the real Brave Search call. Signature:
        (query: str) -> list[dict]
    returning [{"url": str, "title": str, "snippet": str}, ...].
    Both parameters exist so the full loop can be tested end-to-end with no
    real API calls.

    Returns a dict:
        {
            "claim_text": str,
            "status": str,               # see below
            "attempts": int,
            "last_attempt_status": str | None,
        }

    status is one of:
        "verified"                  - a proposal passed the full pipeline
        "too_vague_to_verify"       - rejected by the pre-check, no LLM call
        "unverifiable_after_retries"- attempts exhausted (hard cap or early
                                       stop) without reaching "verified".
                                       A distinct, named state - NOT folded
                                       into any single-attempt failure status.

    last_attempt_status is the literal status string of the last attempt
    (e.g. "ambiguous", "numeric_mismatch", "no_search_results"), or None if
    no attempt was made (too_vague_to_verify). It is a plain string, NOT a
    ClaimTag object: returning the object would let a caller read a single-
    attempt status with no indication it was the last of several exhausted
    retries. The full per-attempt evidence is in the structured JSON-lines log.
    """
    if not is_verifiable_claim(claim_text):
        return {
            "claim_text": claim_text,
            "status": "too_vague_to_verify",
            "attempts": 0,
            "last_attempt_status": None,
        }

    llm_fn = llm_fn or _default_llm_call
    search_fn = search_fn or search_for_source

    history: list[AttemptRecord] = []
    feedback: str | None = None

    for attempt in range(1, max_attempts + 1):
        search_results = search_fn(claim_text)

        if not search_results:
            # No LLM call when search returns nothing. This is a firm rule:
            # falling back to model memory would defeat the reason search was
            # added. See module docstring, "SEARCH LAYER".
            record = AttemptRecord(
                attempt=attempt,
                url="",
                quote="",
                status="no_search_results",
                top_score=None,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            _log_attempt(record, claim_text, log_dir)
            history.append(record)

            if len(history) >= 2 and no_meaningful_progress(history[-2], history[-1]):
                break

            feedback = _NO_SEARCH_RESULTS_FEEDBACK
            continue

        proposal = llm_fn(claim_text, feedback, search_results)
        url, quote = proposal["url"], proposal["quote"]

        tag = verify_bucket_a_claim(
            claim_id=claim_id,
            claim_text=claim_text,
            url=url,
            allowlist=allowlist,
            quote=quote,
            document=document,
        )

        score = tag.quote_evidence.top_score if tag.quote_evidence else None
        record = AttemptRecord(
            attempt=attempt,
            url=url,
            quote=quote,
            status=tag.overall_status,
            top_score=score,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        _log_attempt(record, claim_text, log_dir)
        history.append(record)

        if tag.overall_status == "verified":
            return {
                "claim_text": claim_text,
                "status": "verified",
                "attempts": attempt,
                "last_attempt_status": tag.overall_status,
            }

        # Early stop: two consecutive attempts with no measurable progress.
        if len(history) >= 2 and no_meaningful_progress(history[-2], history[-1]):
            break

        feedback = _build_feedback(tag.overall_status)

    return {
        "claim_text": claim_text,
        "status": "unverifiable_after_retries",
        "attempts": len(history),
        "last_attempt_status": history[-1].status if history else None,
    }
