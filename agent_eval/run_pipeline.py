"""Top-level dispatcher: triage a claim and route it to the right pipeline.

Always returns the same four-field dict (outcome, bucket, triage_reasoning,
tag) regardless of which pipeline ran or whether triage failed, so every
consumer handles exactly one shape.

Key decisions (full reasoning in adr/0020-run-pipeline.md):

- Bucket B never comes from triage — identifying which external framework
  applies is a human decision, supplied as an explicit bucket="B".
- Triage is skipped when a bucket is explicitly supplied: an explicit bucket
  is a human routing decision that overrides the model's judgment, and
  skipping guarantees triage_llm_fn is never called in that case.
- Triage runs at most once per dispatch. When the dispatcher routes to
  Bucket C (whether by its own triage or an explicit bucket="C"), the C
  pipeline receives a stub that replays the routing decision instead of
  re-triaging — a second nondeterministic call would cost a duplicate API
  call and could contradict the routing already made
  (adr/0027-dispatcher-triages-once.md).
- Bucket A's ClaimTag is built via capturing closures around llm_fn and
  fetch_fn: on a "verified" return the captured url/quote/document are
  guaranteed to belong to the successful attempt, because
  extract_claim_evidence returns immediately on first success.
- "search_unavailable" (the search layer could not run at all) is passed
  through as a named outcome for Buckets A, B, and C rather than collapsed
  into "unverifiable"/"incomplete" — a configuration failure must never
  look like an honest verification result (adr/0026).
"""

from typing import Callable, TypedDict

from agent_eval.bucket_b_pipeline import run_bucket_b_pipeline
from agent_eval.bucket_c_pipeline import run_bucket_c_pipeline
from agent_eval.bucket_d_pipeline import run_bucket_d_pipeline
from agent_eval.bucket_triage import triage_claim
from agent_eval.extraction import FetchFn, LLMCallFn, SearchFn
from agent_eval.extraction import default_llm_call as _extraction_default_llm_call
from agent_eval.extraction import extract_claim_evidence
from agent_eval.page_fetch import FetchResult
from agent_eval.pipeline import verify_bucket_a_claim
from agent_eval.tag_schema import ClaimTag
from agent_eval.web_search import SearchResult, SearchUnavailable

_VALID_BUCKETS = {"A", "B", "C", "D"}


class PipelineResult(TypedDict):
    """The dispatcher's four-field return contract — one shape for every
    consumer, regardless of which pipeline ran or whether triage failed."""

    outcome: str
    bucket: str | None
    triage_reasoning: str | None
    tag: ClaimTag | None


def run_pipeline(
    claim_text: str,
    allowlist: list[str],
    *,
    company_name: str,
    claim_id: str,
    bucket: str | None = None,
    criteria: list[str] | None = None,
    log_dir: str = "logs",
    triage_llm_fn: Callable[[str], dict] | None = None,
    extraction_llm_fn: LLMCallFn | None = None,
    extraction_search_fn: SearchFn | None = None,
    extraction_fetch_fn: FetchFn | None = None,
    bucket_b_search_fn: SearchFn | None = None,
    bucket_b_url_llm_fn: (
        Callable[[str, str, str, list[SearchResult]], dict] | None
    ) = None,
    bucket_b_fetch_fn: FetchFn | None = None,
    bucket_b_criterion_evidence_fn: Callable[..., dict] | None = None,
    bucket_c_search_fn: SearchFn | None = None,
    bucket_c_url_llm_fn: Callable[[str, list[SearchResult]], dict] | None = None,
    bucket_c_fetch_fn: FetchFn | None = None,
    bucket_c_finding_llm_fn: Callable[[str, str], dict] | None = None,
    bucket_c_reconciliation_llm_fn: (
        Callable[[str, list[dict], str | None], dict] | None
    ) = None,
    bucket_d_llm_fn: Callable[[str, str | None], dict] | None = None,
) -> PipelineResult:
    """
    Route a claim through triage and into the correct bucket pipeline.

    If `bucket` is supplied, triage is skipped and the claim routes directly
    to that pipeline. Valid values: "A", "B", "C", "D". Any other value
    returns outcome="invalid_bucket".

    If `bucket` is None, triage_claim runs first. "bucket_b" is never a
    triage output — Bucket B always requires explicit bucket="B".

    Returns a dict with exactly four fields:
        {
            "outcome": str,
            "bucket": str | None,
            "triage_reasoning": str | None,
            "tag": ClaimTag | None,
        }
    """
    triage_reasoning = None

    if bucket is not None:
        if bucket not in _VALID_BUCKETS:
            return {
                "outcome": "invalid_bucket",
                "bucket": None,
                "triage_reasoning": None,
                "tag": None,
            }
        actual_bucket = bucket
    else:
        triage_result = triage_claim(claim_text, llm_fn=triage_llm_fn)
        classification = triage_result["classification"]
        triage_reasoning = triage_result["reasoning"]

        if classification == "ambiguous":
            return {
                "outcome": "ambiguous",
                "bucket": None,
                "triage_reasoning": triage_reasoning,
                "tag": None,
            }
        if classification == "malformed_llm_response":
            return {
                "outcome": "triage_failed",
                "bucket": None,
                "triage_reasoning": None,
                "tag": None,
            }

        actual_bucket = {"bucket_a": "A", "bucket_c": "C", "bucket_d": "D"}[
            classification
        ]

    # --- Bucket A ---
    if actual_bucket == "A":
        tag, a_status = _run_bucket_a(
            claim_text=claim_text,
            allowlist=allowlist,
            company_name=company_name,
            claim_id=claim_id,
            extraction_llm_fn=extraction_llm_fn,
            extraction_search_fn=extraction_search_fn,
            extraction_fetch_fn=extraction_fetch_fn,
            log_dir=log_dir,
        )
        if tag is not None:
            outcome = "verified"
        elif a_status == "search_unavailable":
            # Named infrastructure failure, never collapsed into
            # "unverifiable" — that word means the claim was tried against
            # real sources and could not be verified (adr/0026).
            outcome = "search_unavailable"
        else:
            outcome = "unverifiable"
        return {
            "outcome": outcome,
            "bucket": "A",
            "triage_reasoning": triage_reasoning,
            "tag": tag,
        }

    # --- Bucket B ---
    if actual_bucket == "B":
        try:
            tag_b = run_bucket_b_pipeline(
                company_name=company_name,
                claim_id=claim_id,
                allowlist=allowlist,
                criteria=criteria,
                search_fn=bucket_b_search_fn,
                url_llm_fn=bucket_b_url_llm_fn,
                fetch_fn=bucket_b_fetch_fn,
                criterion_evidence_fn=bucket_b_criterion_evidence_fn,
                log_dir=log_dir,
            )
        except SearchUnavailable:
            return {
                "outcome": "search_unavailable",
                "bucket": "B",
                "triage_reasoning": None,
                "tag": None,
            }
        return {
            "outcome": tag_b.overall_status,
            "bucket": "B",
            "triage_reasoning": None,
            "tag": tag_b,
        }

    # --- Bucket C ---
    if actual_bucket == "C":
        # The routing decision is already made — by the dispatcher's own
        # triage above, or by the caller's explicit bucket="C". Inject a stub
        # that replays it so bucket_c_pipeline never re-triages: a second
        # nondeterministic call would cost a duplicate API call and could
        # contradict the routing already decided (adr/0027).
        _routing_reason = (
            triage_reasoning
            if triage_reasoning is not None
            else "explicitly routed by caller"
        )

        def _c_triage_stub(_claim: str) -> dict:
            return {"classification": "bucket_c", "reasoning": _routing_reason}

        c_result = run_bucket_c_pipeline(
            claim_text=claim_text,
            allowlist=allowlist,
            company_name=company_name,
            claim_id=claim_id,
            triage_llm_fn=_c_triage_stub,
            search_fn=bucket_c_search_fn,
            url_llm_fn=bucket_c_url_llm_fn,
            fetch_fn=bucket_c_fetch_fn,
            finding_llm_fn=bucket_c_finding_llm_fn,
            reconciliation_llm_fn=bucket_c_reconciliation_llm_fn,
            log_dir=log_dir,
        )
        c_outcome = c_result["outcome"]
        outcome = (
            c_result["tag"].overall_status if c_outcome == "completed" else c_outcome
        )
        return {
            "outcome": outcome,
            "bucket": "C",
            "triage_reasoning": triage_reasoning,
            "tag": c_result["tag"],
        }

    # --- Bucket D ---
    tag = run_bucket_d_pipeline(
        claim_text=claim_text,
        company_name=company_name,
        claim_id=claim_id,
        llm_fn=bucket_d_llm_fn,
        log_dir=log_dir,
    )
    return {
        "outcome": tag.overall_status,
        "bucket": "D",
        "triage_reasoning": triage_reasoning,
        "tag": tag,
    }


def _run_bucket_a(
    claim_text: str,
    allowlist: list[str],
    company_name: str,
    claim_id: str,
    extraction_llm_fn: LLMCallFn | None,
    extraction_search_fn: SearchFn | None,
    extraction_fetch_fn: FetchFn | None,
    log_dir: str,
) -> tuple[ClaimTag | None, str]:
    """
    Run Bucket A extraction. Returns (tag, extraction_status): a ClaimTag on
    success (None otherwise) plus extract_claim_evidence's status string, so
    the dispatcher can distinguish named infrastructure failures
    ("search_unavailable") from a claim that genuinely could not be verified.

    Wraps llm_fn and fetch_fn with capturing closures so that on a
    "verified" result the url, quote, and document from the successful
    attempt are available to pass to verify_bucket_a_claim. See module
    docstring for rationale.
    """
    from agent_eval.page_fetch import fetch_page_text

    _captured: dict[str, str] = {
        "url": "",
        "quote": "",
        "document": "",
        "final_url": "",
    }

    def _cap_llm(ct: str, fb: str | None, sr: list[SearchResult]) -> dict:
        fn = extraction_llm_fn or _extraction_default_llm_call
        result = fn(ct, fb, sr)
        _captured["url"] = result.get("url", "")
        _captured["quote"] = result.get("quote", "")
        return result

    def _cap_fetch(url: str) -> FetchResult:
        fn = extraction_fetch_fn or fetch_page_text
        result = fn(url)
        if result.get("success"):
            _captured["document"] = result.get("text") or ""
            _captured["final_url"] = result.get("final_url") or ""
        return result

    a_result = extract_claim_evidence(
        claim_text=claim_text,
        allowlist=allowlist,
        company_name=company_name,
        claim_id=claim_id,
        llm_fn=_cap_llm,
        search_fn=extraction_search_fn,
        fetch_fn=_cap_fetch,
        log_dir=log_dir,
    )

    if a_result["status"] != "verified":
        return None, a_result["status"]

    # Rebuild the tag against the post-redirect URL when the fetch layer
    # reported one, mirroring extraction.py's re-validation
    # (adr/0023-redirect-revalidation.md). Fakes without final_url fall back
    # to the proposed URL.
    tag = verify_bucket_a_claim(
        claim_id=claim_id,
        claim_text=claim_text,
        url=_captured["final_url"] or _captured["url"],
        allowlist=allowlist,
        quote=_captured["quote"],
        document=_captured["document"],
    )
    return tag, a_result["status"]
