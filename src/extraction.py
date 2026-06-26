"""
extraction.py

The AI extraction layer - the ONLY place in this project that calls a real
LLM. It asks a model to select the best candidate source URL from real web
search results and propose a supporting quote, then fetches that URL's content
and runs the proposal through the existing, fully-deterministic pipeline
(pipeline.verify_bucket_a_claim) to check whether the self-report actually
holds up.

Everything downstream of this file - domain_check, quote_match, tag_schema,
pipeline - stays exactly as built: no model, no randomness, fully testable
with no API key. This module is the single boundary where non-determinism
enters, and it is deliberately thin.

INDEPENDENT GROUND TRUTH - why the LLM does not see the document:

The model is given the claim text and real search-result candidates (URLs
+ snippets). The source document is fetched live from the URL the model
selects - it is not supplied by the caller or by the model. The legitimacy
`allowlist` is supplied by the caller and checked against the model's URL
proposal. This is the whole point: the document content comes from the
actual URL, not from a caller-supplied string, and the allowlist is
verified independently of the model's proposal. A hallucinated source
cannot validate itself - exactly the non-discriminating-verification
failure this project exists to prevent.

(Note: this is why the public function takes `allowlist` in addition to
`claim_text`, rather than `claim_text` alone - the allowlist is the
independent ground truth that the model's URL proposal is checked against.
The document itself is now fetched live, not caller-supplied, which closes
the gap where a test fixture could stand in for real page content.)

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

FETCH LAYER - why the document is fetched live, not caller-supplied:

After the model selects a URL (confirmed to be from the search results via
url_compare.same_url), that URL is fetched live via page_fetch.fetch_page_text.
The fetched text becomes the document that quote_match checks the proposed
quote against. This closes the last gap in the pipeline: previously,
extraction.py accepted `document` as a caller-supplied parameter, which
meant the document content in tests was a hardcoded fixture rather than the
real page at the model's proposed URL. Now, whether in tests or in a live
run, the verification always runs against actual fetched content. Test
isolation is preserved by injecting a fake `fetch_fn` (same pattern as
`llm_fn` and `search_fn`) so unit tests can control the returned text without
making real HTTP calls.

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
attempts share the same stage_reached and the same status without the
quote-match score meaningfully improving. Reaching a later pipeline stage
(e.g. advancing from fetch_failed to verification_completed) always counts
as progress even if the later stage then fails. The failure mode being
managed here is WRONG CONTENT (a hallucinated quote, a bad URL), not an
overloaded or rate-limited API - so exponential backoff does not apply.
Waiting longer does not make a non-existent source exist. Repeated retries
that aren't measurably moving toward "verified" are a signal that the claim
may not be backed by a findable source at all, and continuing just burns
API cost.

STRUCTURED LOG - so a human can audit failures, not just trust the verdict:

Every attempt (not only the final outcome) is appended to a JSON-lines log
in logs/. Each log entry includes a stage_reached field indicating how far
that attempt got ("no_search_results", "url_not_from_search_results",
"fetch_failed", or "verification_completed"), so a human scanning the log
can immediately see where each attempt stopped. The purpose is to let a
human review failures later and spot patterns (a certain kind of claim
failing the same way), and to independently check whether a status was
actually correct rather than trusting the system's own verdict about its
own failure - the same "don't trust a confident result without the evidence
to check it" principle the rest of this project is built on.
"""

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from dotenv import load_dotenv

from log_utils import append_log_entry
from page_fetch import fetch_page_text
from pipeline import verify_bucket_a_claim
from url_compare import same_url
from web_search import search_for_source

load_dotenv()

# Default model: the cheapest capable nano tier (simple extraction, not a
# reasoning task). Overridable via OPENAI_MODEL so the exact current-cheapest
# model can be swapped in without a code change as OpenAI's lineup shifts.
MODEL = os.getenv("OPENAI_MODEL", "gpt-5-nano")

# Hard ceiling on LLM calls per claim, regardless of anything else.
MAX_ATTEMPTS = 3

# Two consecutive same-stage, same-status attempts whose top quote-match score
# improves by less than this many points count as "no meaningful progress" and
# stop the loop early. This threshold is a STARTING ASSUMPTION, not derived
# from data - documented as such, like AMBIGUITY_GAP_THRESHOLD in
# quote_match.py. Revisit against real logged runs once there are some.
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
    # Reuses tag_schema's overall_status vocabulary for verification_completed
    # attempts. For earlier-stage failures the status holds the specific
    # failure reason for that stage (e.g. "not_found" for a fetch_failed
    # attempt), so a human reading the log knows exactly what failed.
    status: str
    top_score: float | None
    # How far this attempt got through the pipeline before stopping.
    # Possible values: "no_search_results", "url_not_from_search_results",
    # "fetch_failed", "verification_completed".
    stage_reached: str
    timestamp: str
    company_name: str = ""


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
    True if `current` did not meaningfully improve on `previous`.

    Different stage_reached always counts as progress: advancing further
    through the pipeline (e.g. from fetch_failed to verification_completed)
    is forward movement even if the later stage then fails. Only two
    consecutive attempts stuck at the SAME stage with the same specific
    reason and no score improvement count as no progress.

    A None score is treated as 0.0 for comparison. Different statuses at
    the same stage also count as progress (the system is exploring a
    different failure within that stage).
    """
    if previous.stage_reached != current.stage_reached:
        return False
    if previous.status != current.status:
        return False
    prev_score = previous.top_score if previous.top_score is not None else 0.0
    curr_score = current.top_score if current.top_score is not None else 0.0
    return (curr_score - prev_score) < threshold


def _build_feedback(status: str) -> str:
    """
    Turn a specific verification failure status into corrective guidance for
    the next LLM call. Only handles ClaimTag.overall_status values; fetch
    failures use _build_fetch_feedback instead.
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
    if status == "url_not_from_search_results":
        return (
            "the URL you proposed was not one of the search results provided - "
            "you must select a URL exactly from the candidates list provided"
        )
    return "verification failed - provide a different source URL and exact quote"


def _build_fetch_feedback(failure_reason: str) -> str:
    """
    Turn a page_fetch failure_reason into corrective guidance for the next
    LLM call. Kept separate from _build_feedback, which handles only
    ClaimTag.overall_status values from the verification stage.
    """
    if failure_reason == "not_found":
        return (
            "the URL returned a 404 Not Found - try a different URL "
            "from the search results"
        )
    if failure_reason == "forbidden":
        return (
            "the URL returned 403 Forbidden - the page may require "
            "authentication; try a different URL from the search results"
        )
    if failure_reason == "timeout":
        return (
            "the URL timed out before responding - try a different URL "
            "from the search results"
        )
    if failure_reason in ("too_large", "size_unknown"):
        return (
            "the document could not be fetched due to size constraints - "
            "try a different URL, preferably a specific page rather than "
            "a large PDF"
        )
    if failure_reason == "unsupported_content_type":
        return (
            "the URL did not return a web page or PDF - try a different "
            "URL from the search results"
        )
    return (
        "the URL could not be fetched - try a different URL from the " "search results"
    )


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
    """Append one attempt to the shared evaluation log."""
    append_log_entry(
        {
            "timestamp": record.timestamp,
            "bucket": "A",
            "company_name": record.company_name,
            "claim_text": claim_text,
            "attempt": record.attempt,
            "url": record.url,
            "quote": record.quote,
            "status": record.status,
            "top_score": record.top_score,
            "stage_reached": record.stage_reached,
        },
        log_dir,
    )


def extract_claim_evidence(
    claim_text: str,
    allowlist: list[str],
    *,
    company_name: str,
    claim_id: str = "claim",
    llm_fn=None,
    search_fn=None,
    fetch_fn=None,
    log_dir: str = "logs",
    max_attempts: int = MAX_ATTEMPTS,
) -> dict:
    """
    Extract and verify a source URL + quote for `claim_text`.

    `allowlist` is the independent ground truth the model's URL proposal is
    checked against - it is NOT shown to the model. The document is fetched
    live from whatever URL the model proposes (after it passes the search-
    results check), so there is no caller-supplied document.

    `llm_fn`, if provided, replaces the real OpenAI call. Signature:
        (claim_text: str, feedback: str | None, search_results: list[dict])
        -> dict
    returning {"url": str, "quote": str}.

    `search_fn`, if provided, replaces the real Brave Search call. Signature:
        (query: str) -> list[dict]
    returning [{"url": str, "title": str, "snippet": str}, ...].

    `fetch_fn`, if provided, replaces the real page_fetch.fetch_page_text
    call. Signature:
        (url: str) -> dict
    returning {"success": bool, "text": str|None, "content_type": str|None,
               "failure_reason": str|None}.

    All three parameters exist so the full loop can be tested end-to-end
    with no real API or HTTP calls.

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
    fetch_fn = fetch_fn or fetch_page_text

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
                stage_reached="no_search_results",
                top_score=None,
                timestamp=datetime.now(timezone.utc).isoformat(),
                company_name=company_name,
            )
            _log_attempt(record, claim_text, log_dir)
            history.append(record)

            if len(history) >= 2 and no_meaningful_progress(history[-2], history[-1]):
                break

            feedback = _NO_SEARCH_RESULTS_FEEDBACK
            continue

        try:
            proposal = llm_fn(claim_text, feedback, search_results)
            url = proposal["url"]
            quote = proposal["quote"]
            if not isinstance(url, str) or not isinstance(quote, str):
                raise ValueError("url and quote must both be strings")
        except Exception:
            # The LLM returned something unparseable or missing required fields.
            # Convert to a named failure, not an uncaught crash. This has not
            # fired in any live run so far (JSON mode has been reliable), but
            # leaving it unhandled would crash the entire loop on first
            # occurrence rather than treating it as a retryable attempt.
            record = AttemptRecord(
                attempt=attempt,
                url="",
                quote="",
                status="malformed_llm_response",
                stage_reached="malformed_llm_response",
                top_score=None,
                timestamp=datetime.now(timezone.utc).isoformat(),
                company_name=company_name,
            )
            _log_attempt(record, claim_text, log_dir)
            history.append(record)

            if len(history) >= 2 and no_meaningful_progress(history[-2], history[-1]):
                break

            feedback = (
                "your previous response could not be parsed - respond with "
                "ONLY a valid JSON object containing exactly the fields "
                "'url' and 'quote'"
            )
            continue

        # Enforce that the proposed URL actually came from the search results.
        # Prompt instructions alone are unverified trust; this is the
        # deterministic check. same_url() tolerates trivial formatting
        # differences (http/https, www., trailing slash) so a model that
        # returns a URL verbatim from the candidates list always passes.
        if not any(same_url(url, r["url"]) for r in search_results):
            record = AttemptRecord(
                attempt=attempt,
                url=url,
                quote=quote,
                status="url_not_from_search_results",
                stage_reached="url_not_from_search_results",
                top_score=None,
                timestamp=datetime.now(timezone.utc).isoformat(),
                company_name=company_name,
            )
            _log_attempt(record, claim_text, log_dir)
            history.append(record)

            if len(history) >= 2 and no_meaningful_progress(history[-2], history[-1]):
                break

            feedback = _build_feedback("url_not_from_search_results")
            continue

        fetch_result = fetch_fn(url)
        if not fetch_result["success"]:
            failure_reason = fetch_result["failure_reason"]
            record = AttemptRecord(
                attempt=attempt,
                url=url,
                quote=quote,
                status=failure_reason,
                stage_reached="fetch_failed",
                top_score=None,
                timestamp=datetime.now(timezone.utc).isoformat(),
                company_name=company_name,
            )
            _log_attempt(record, claim_text, log_dir)
            history.append(record)

            if len(history) >= 2 and no_meaningful_progress(history[-2], history[-1]):
                break

            feedback = _build_fetch_feedback(failure_reason)
            continue

        tag = verify_bucket_a_claim(
            claim_id=claim_id,
            claim_text=claim_text,
            url=url,
            allowlist=allowlist,
            quote=quote,
            document=fetch_result["text"],
        )

        score = tag.quote_evidence.top_score if tag.quote_evidence else None
        record = AttemptRecord(
            attempt=attempt,
            url=url,
            quote=quote,
            status=tag.overall_status,
            stage_reached="verification_completed",
            top_score=score,
            timestamp=datetime.now(timezone.utc).isoformat(),
            company_name=company_name,
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
