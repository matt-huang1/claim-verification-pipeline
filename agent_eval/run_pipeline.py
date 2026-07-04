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
- Bucket A's ClaimTag is built via capturing closures around llm_fn and
  fetch_fn: on a "verified" return the captured url/quote/document are
  guaranteed to belong to the successful attempt, because
  extract_claim_evidence returns immediately on first success.
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
from agent_eval.web_search import SearchResult

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
        tag = _run_bucket_a(
            claim_text=claim_text,
            allowlist=allowlist,
            company_name=company_name,
            claim_id=claim_id,
            extraction_llm_fn=extraction_llm_fn,
            extraction_search_fn=extraction_search_fn,
            extraction_fetch_fn=extraction_fetch_fn,
            log_dir=log_dir,
        )
        return {
            "outcome": "verified" if tag is not None else "unverifiable",
            "bucket": "A",
            "triage_reasoning": triage_reasoning,
            "tag": tag,
        }

    # --- Bucket B ---
    if actual_bucket == "B":
        tag = run_bucket_b_pipeline(
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
        return {
            "outcome": tag.overall_status,
            "bucket": "B",
            "triage_reasoning": None,
            "tag": tag,
        }

    # --- Bucket C ---
    if actual_bucket == "C":
        # When explicitly routed to C, override triage inside bucket_c_pipeline
        # so it never re-classifies the claim. When triage ran at dispatcher
        # level, pass the real fn so test fakes propagate correctly.
        _c_triage_fn = triage_llm_fn
        if bucket == "C":

            def _explicit_c_triage(_claim: str) -> dict:
                return {
                    "classification": "bucket_c",
                    "reasoning": "explicitly routed by caller",
                }

            _c_triage_fn = _explicit_c_triage

        c_result = run_bucket_c_pipeline(
            claim_text=claim_text,
            allowlist=allowlist,
            company_name=company_name,
            claim_id=claim_id,
            triage_llm_fn=_c_triage_fn,
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
) -> ClaimTag | None:
    """
    Run Bucket A extraction. Returns a ClaimTag on success, None otherwise.

    Wraps llm_fn and fetch_fn with capturing closures so that on a
    "verified" result the url, quote, and document from the successful
    attempt are available to pass to verify_bucket_a_claim. See module
    docstring for rationale.
    """
    from agent_eval.page_fetch import fetch_page_text

    _captured: dict[str, str] = {"url": "", "quote": "", "document": ""}

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
        return None

    return verify_bucket_a_claim(
        claim_id=claim_id,
        claim_text=claim_text,
        url=_captured["url"],
        allowlist=allowlist,
        quote=_captured["quote"],
        document=_captured["document"],
    )
