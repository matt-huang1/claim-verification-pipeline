"""
bucket_c_pipeline.py

Orchestrator for Bucket C: given a claim text and an allowlist, runs
triage → source gathering → reconciliation and returns a result dict
describing the outcome.

DESIGN DECISIONS:

WHY target_source_count IS NOT EXPOSED AT THE ORCHESTRATOR LEVEL:

target_source_count is intentionally not a parameter of
run_bucket_c_pipeline. It uses gather_source_findings's own default
(currently 5). The orchestrator's job is to wire steps together, not
to re-expose every knob of every step. Exposing target_source_count
here would imply the orchestrator has an opinion about what the right
value is for a given claim type — it does not. If a real live run shows
that a specific claim type consistently needs more or fewer sources,
the right place to change this is gather_source_findings's default, not
a per-call override at the orchestrator level.

WHY THE ORCHESTRATOR ADDS NO LOGGING OF ITS OWN:

reconcile_sources already writes one structured log entry per call
covering the full Bucket C outcome (sources_checked, groups_found,
distinct_count, unresolved_count, outcome, etc.). Adding a second
orchestrator-level entry would duplicate information already in the log.
This contrasts with bucket_b_pipeline.py, which IS the only place that
logs (criterion_evidence.py has no logging of its own). Here each step's
own module handles its concerns; the orchestrator just wires them.

WHY NO RETRY LOGIC:

Each underlying function handles its own retries. triage_claim's
"ambiguous" outcome is a stable finding, never retried.
reconcile_sources retries the LLM call internally (up to
_MAX_RECONCILIATION_ATTEMPTS) and surfaces failure honestly in the
returned SourcePluralityEvidence. Adding retry at the orchestrator level
would paper over failures that are already honestly reported by the
functions themselves.
"""

from bucket_triage import triage_claim
from reconciliation import reconcile_sources
from source_extraction import gather_source_findings
from tag_schema import ClaimTag


def run_bucket_c_pipeline(
    claim_text: str,
    allowlist: list[str],
    *,
    company_name: str,
    claim_id: str,
    triage_llm_fn=None,
    search_fn=None,
    url_llm_fn=None,
    fetch_fn=None,
    finding_llm_fn=None,
    reconciliation_llm_fn=None,
    log_dir: str = "logs",
) -> dict:
    """
    Run the full Bucket C pipeline for `claim_text`.

    Steps:
      1. triage_claim — route the claim to bucket_a, bucket_c, ambiguous,
         or malformed_llm_response.
      2. gather_source_findings — search → URL selection → fetch →
         per-source extraction (SourceFinding list).
      3. reconcile_sources — group definition-bearing sources by shared
         real-world scope; returns SourcePluralityEvidence.
      4. Build and return a ClaimTag with bucket="C".

    Steps 2-4 run only when triage returns "bucket_c". Any other triage
    result returns immediately without calling gather or reconcile.

    Returns a dict with four possible shapes:

        {"outcome": "routed_to_bucket_a",
         "triage_reasoning": str,
         "tag": None}

        {"outcome": "ambiguous",
         "triage_reasoning": str,
         "tag": None}

        {"outcome": "triage_failed",
         "triage_reasoning": None,
         "tag": None}

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

    if classification == "malformed_llm_response":
        return {
            "outcome": "triage_failed",
            "triage_reasoning": None,
            "tag": None,
        }

    # classification == "bucket_c" — continue

    # --- Step 2: gather source findings ---
    findings = gather_source_findings(
        claim_text=claim_text,
        allowlist=allowlist,
        search_fn=search_fn,
        url_llm_fn=url_llm_fn,
        fetch_fn=fetch_fn,
        finding_llm_fn=finding_llm_fn,
    )

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
