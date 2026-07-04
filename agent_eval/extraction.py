"""Bucket A extraction: the single point where a real model is reached.

The flow: web-search the claim, hand the model the real candidate URLs, let it
propose one URL plus a supporting quote, fetch that URL live, and run the
proposal through the deterministic pipeline (verify_bucket_a_claim).

Four properties make this a real check rather than the model grading its own
homework: independent ground truth (the model never sees the document or the
allowlist); the firm no-fallback rule (empty search means no LLM call — no
path back to proposing a URL from model memory); retry stopping by progress,
not backoff (wrong content does not improve by waiting); and a cheap
deterministic pre-check gate before any API spend. Every attempt is appended
to the JSONL log with its stage_reached, so a human can audit where each
attempt stopped rather than trust the system's own verdict.

Full design trail — rejected search providers, model-choice rationale, and
the live run that first demonstrated the failure mode — in
adr/0006-extraction.md.
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol, TypedDict, cast

from agent_eval.llm_client import default_complete_json
from agent_eval.log_utils import append_log_entry
from agent_eval.page_fetch import FetchResult, fetch_page_text
from agent_eval.pipeline import verify_bucket_a_claim
from agent_eval.url_compare import same_url
from agent_eval.web_search import SearchResult, search_for_source

# The three injectable seams of the Bucket A loop. Tests inject fakes with
# these shapes; production leaves them None and gets the real implementations.
LLMCallFn = Callable[[str, "str | None", "list[SearchResult]"], dict]
SearchFn = Callable[[str], "list[SearchResult]"]
FetchFn = Callable[[str], FetchResult]


class ExtractionResult(TypedDict):
    """extract_claim_evidence's return contract."""

    claim_text: str
    status: str
    attempts: int
    last_attempt_status: str | None


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
    # Possible values: "no_search_results", "malformed_llm_response",
    # "url_not_from_search_results", "fetch_failed",
    # "verification_completed".
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


class _ProgressAttempt(Protocol):
    """
    The structural contract no_meaningful_progress() depends on: any record
    carrying these three fields can be passed to it. Both AttemptRecord
    (Bucket A) and criterion_evidence.CriterionAttemptRecord (Bucket B)
    satisfy this without a shared base class.
    """

    stage_reached: str
    status: str
    top_score: float | None


def no_meaningful_progress(
    previous: _ProgressAttempt,
    current: _ProgressAttempt,
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
    claim_text: str, feedback: str | None, search_results: list[SearchResult]
) -> dict:
    """
    The real LLM call. Presents real search candidates to the model and asks
    it to select the best matching URL and propose a verbatim supporting quote.
    This is the only function here that reaches a real model (via the shared
    llm_client); tests inject a fake in its place via the `llm_fn` parameter.
    """
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

    data = json.loads(default_complete_json(system, user))
    return {"url": data["url"], "quote": data["quote"]}


def default_llm_call(
    claim_text: str, feedback: str | None, search_results: list[SearchResult]
) -> dict:
    """
    Public wrapper around the real LLM call. Exposed so callers such as
    run_pipeline.py can reference it without accessing a private symbol.
    Delegates entirely to _default_llm_call.
    """
    return _default_llm_call(claim_text, feedback, search_results)


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
    llm_fn: LLMCallFn | None = None,
    search_fn: SearchFn | None = None,
    fetch_fn: FetchFn | None = None,
    log_dir: str = "logs",
    max_attempts: int = MAX_ATTEMPTS,
) -> ExtractionResult:
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

    `search_fn`, if provided, replaces the real Tavily search call. Signature:
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
            # success=False guarantees failure_reason is a str (FetchResult
            # contract); the cast is annotation-only.
            failure_reason = cast(str, fetch_result["failure_reason"])
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

        # success=True guarantees text is a str (FetchResult contract); the
        # cast is annotation-only.
        tag = verify_bucket_a_claim(
            claim_id=claim_id,
            claim_text=claim_text,
            url=url,
            allowlist=allowlist,
            quote=quote,
            document=cast(str, fetch_result["text"]),
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
