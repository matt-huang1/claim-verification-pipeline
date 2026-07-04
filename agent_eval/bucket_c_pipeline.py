"""Bucket C orchestrator: triage -> source gathering -> reconciliation.

Wiring only. target_source_count is deliberately not re-exposed here (the
orchestrator has no opinion about the right value — that lives with
gather_source_findings's default); no orchestrator-level logging (each
step's own module already writes its structured entry); and no retry logic
(each underlying function handles its own retries, and orchestrator-level
retry would paper over failures they already report honestly).
Design context: adr/0013-designing-bucket-c.md and adr/0018-reconciliation.md.
"""

from typing import Callable

from agent_eval.bucket_triage import triage_claim
from agent_eval.page_fetch import FetchResult
from agent_eval.reconciliation import reconcile_sources
from agent_eval.source_extraction import gather_source_findings
from agent_eval.tag_schema import ClaimTag
from agent_eval.web_search import SearchResult, SearchUnavailable


def run_bucket_c_pipeline(
    claim_text: str,
    allowlist: list[str],
    *,
    company_name: str,
    claim_id: str,
    triage_llm_fn: Callable[[str], dict] | None = None,
    search_fn: Callable[[str], list[SearchResult]] | None = None,
    url_llm_fn: Callable[[str, list[SearchResult]], dict] | None = None,
    fetch_fn: Callable[[str], FetchResult] | None = None,
    finding_llm_fn: Callable[[str, str], dict] | None = None,
    reconciliation_llm_fn: Callable[[str, list[dict], str | None], dict] | None = None,
    log_dir: str = "logs",
) -> dict:
    """
    Run the full Bucket C pipeline for `claim_text`.

    Steps:
      1. triage_claim — route the claim to bucket_a, bucket_c, bucket_d,
         ambiguous, or malformed_llm_response.
      2. gather_source_findings — search → URL selection → fetch →
         per-source extraction (SourceFinding list).
      3. reconcile_sources — group definition-bearing sources by shared
         real-world scope; returns SourcePluralityEvidence.
      4. Build and return a ClaimTag with bucket="C".

    Steps 2-4 run only when triage returns "bucket_c". Any other triage
    result returns immediately without calling gather or reconcile.

    Returns a dict with six possible shapes:

        {"outcome": "routed_to_bucket_a",
         "triage_reasoning": str,
         "tag": None}

        {"outcome": "routed_to_bucket_d",
         "triage_reasoning": str,
         "tag": None}

        {"outcome": "ambiguous",
         "triage_reasoning": str,
         "tag": None}

        {"outcome": "triage_failed",
         "triage_reasoning": None,
         "tag": None}

        {"outcome": "search_unavailable",
         "triage_reasoning": str,
         "tag": None}
        (the search layer could not run at all and no findings were
         gathered — a configuration/infrastructure failure, named so it
         can never look like an honest "no consensus" result; adr/0026)

        {"outcome": "completed",
         "triage_reasoning": str,
         "tag": ClaimTag}

    Injectable fakes for testing (no real API calls needed in unit tests):

        triage_llm_fn(claim_text: str) -> dict
            Returns {"classification": str, "reasoning": str}.

        search_fn(query: str) -> list[dict]
            Returns [{"url": str, "title": str, "snippet": str}, ...].

        url_llm_fn(claim_text, search_results) -> {"url": str}

        fetch_fn(url: str) -> {"success": bool, "text": str|None,
                               "content_type": str|None,
                               "failure_reason": str|None}

        finding_llm_fn(document: str, claim_text: str) -> dict
            Five-field response: value_found, claimed_value,
            is_literal_value, definition_found, definition_text.

        reconciliation_llm_fn(claim_text: str, findings: list[dict],
                              feedback: str | None) -> dict
            Returns {"groups": [...], "distinct": [...],
                     "unresolved": [...]}.
    """
    # --- Step 1: triage ---
    triage_result = triage_claim(claim_text, llm_fn=triage_llm_fn)
    classification = triage_result["classification"]
    reasoning = triage_result["reasoning"]

    if classification == "bucket_a":
        return {
            "outcome": "routed_to_bucket_a",
            "triage_reasoning": reasoning,
            "tag": None,
        }

    if classification == "ambiguous":
        return {
            "outcome": "ambiguous",
            "triage_reasoning": reasoning,
            "tag": None,
        }

    if classification == "bucket_d":
        return {
            "outcome": "routed_to_bucket_d",
            "triage_reasoning": reasoning,
            "tag": None,
        }

    if classification == "malformed_llm_response":
        return {
            "outcome": "triage_failed",
            "triage_reasoning": None,
            "tag": None,
        }

    # classification == "bucket_c" — continue

    # --- Step 2: gather source findings ---
    try:
        findings = gather_source_findings(
            claim_text=claim_text,
            allowlist=allowlist,
            search_fn=search_fn,
            url_llm_fn=url_llm_fn,
            fetch_fn=fetch_fn,
            finding_llm_fn=finding_llm_fn,
        )
    except SearchUnavailable:
        return {
            "outcome": "search_unavailable",
            "triage_reasoning": reasoning,
            "tag": None,
        }

    # --- Step 3: reconcile ---
    evidence = reconcile_sources(
        claim_text=claim_text,
        findings=findings,
        company_name=company_name,
        llm_fn=reconciliation_llm_fn,
        log_dir=log_dir,
    )

    # --- Step 4: build ClaimTag ---
    tag = ClaimTag(
        claim_id=claim_id,
        claim_text=claim_text,
        bucket="C",
        source_plurality_evidence=evidence,
    )

    return {
        "outcome": "completed",
        "triage_reasoning": reasoning,
        "tag": tag,
    }
