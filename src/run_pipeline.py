"""
run_pipeline.py

Top-level dispatcher that routes a claim through triage and into the
correct bucket pipeline, returning a consistent dict shape regardless of
which bucket ran.

WHY BUCKET B NEVER COMES FROM TRIAGE:
    Triage distinguishes three structural categories: claims with a single
    authoritative source (Bucket A), claims whose underlying category is
    definitionally contested (Bucket C), and claims that are uncheckable
    in principle (Bucket D). Bucket B covers a company's alignment with a
    specific framework (NZIF), which requires a human to identify which
    framework applies and which criteria to check — triage has no basis
    for making that determination from claim text alone. Bucket B always
    requires explicit bucket="B" from the caller.

HOW BUCKET A'S CLAIMTAG IS BUILT:
    extract_claim_evidence's return dict contains {"claim_text", "status",
    "attempts", "last_attempt_status"} — no url, quote, or ClaimTag. To
    build a ClaimTag on a "verified" outcome, the dispatcher wraps both
    the llm_fn and fetch_fn passed to extract_claim_evidence with capturing
    closures that record the last url/quote proposed by the LLM and the
    last successfully fetched document. On a "verified" return, these three
    values are guaranteed to belong to the successful attempt because
    extract_claim_evidence returns immediately on first success. The
    captured values are passed to verify_bucket_a_claim to build a real
    ClaimTag. No log reading required.

WHY TRIAGE IS SKIPPED WHEN BUCKET IS EXPLICITLY SUPPLIED:
    An explicit bucket is a human-supplied routing decision that overrides
    the model's judgment. Calling triage anyway would be wasted cost and
    could produce a contradictory routing that the dispatcher would then
    have to ignore. Skipping triage when the answer is already known is
    also the only way to guarantee that triage_llm_fn is never called in
    tests that supply bucket= explicitly.

WHY THE RETURN SHAPE IS ALWAYS A DICT WITH THE SAME FOUR FIELDS:
    The front-end contract must handle exactly one shape regardless of
    which bucket ran or whether triage failed. One dict with four named
    fields — outcome, bucket, triage_reasoning, tag — lets every consumer
    check result["outcome"] and result["tag"] without knowing or caring
    which pipeline produced the result.
"""

from bucket_b_pipeline import run_bucket_b_pipeline
from bucket_c_pipeline import run_bucket_c_pipeline
from bucket_d_pipeline import run_bucket_d_pipeline
from bucket_triage import triage_claim
from extraction import extract_claim_evidence
from pipeline import verify_bucket_a_claim

_VALID_BUCKETS = {"A", "B", "C", "D"}


def run_pipeline(
    claim_text: str,
    allowlist: list[str],
    *,
    company_name: str,
    claim_id: str,
    bucket: str | None = None,
    criteria: list[str] | None = None,
    log_dir: str = "logs",
    triage_llm_fn=None,
    extraction_llm_fn=None,
    extraction_search_fn=None,
    extraction_fetch_fn=None,
    bucket_b_search_fn=None,
    bucket_b_url_llm_fn=None,
    bucket_b_fetch_fn=None,
    bucket_b_criterion_evidence_fn=None,
    bucket_c_search_fn=None,
    bucket_c_url_llm_fn=None,
    bucket_c_fetch_fn=None,
    bucket_c_finding_llm_fn=None,
    bucket_c_reconciliation_llm_fn=None,
    bucket_d_llm_fn=None,
) -> dict:
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
        if bucket == "C":

            def _c_triage_fn(_claim):
                return {
                    "classification": "bucket_c",
                    "reasoning": "explicitly routed by caller",
                }

        else:
            _c_triage_fn = triage_llm_fn

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
    claim_text,
    allowlist,
    company_name,
    claim_id,
    extraction_llm_fn,
    extraction_search_fn,
    extraction_fetch_fn,
    log_dir,
):
    """
    Run Bucket A extraction. Returns a ClaimTag on success, None otherwise.

    Wraps llm_fn and fetch_fn with capturing closures so that on a
    "verified" result the url, quote, and document from the successful
    attempt are available to pass to verify_bucket_a_claim. See module
    docstring for rationale.
    """
    import extraction as _ext
    from page_fetch import fetch_page_text

    _captured: dict = {"url": "", "quote": "", "document": ""}

    def _cap_llm(ct, fb, sr):
        fn = extraction_llm_fn or _ext._default_llm_call
        result = fn(ct, fb, sr)
        _captured["url"] = result.get("url", "")
        _captured["quote"] = result.get("quote", "")
        return result

    def _cap_fetch(url):
        fn = extraction_fetch_fn or fetch_page_text
        result = fn(url)
        if result.get("success"):
            _captured["document"] = result.get("text", "")
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
