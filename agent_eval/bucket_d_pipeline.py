"""Bucket D orchestrator: analyze_assumptions wrapped in a ClaimTag.

Routing to Bucket D happens upstream (an explicit bucket="D" or the
dispatcher's triage), so there is no triage step here. The return type is
ClaimTag directly, not a dict: analyze_assumptions always returns a
well-formed AssumptionsStatedEvidence (never None, never raises), so there
is exactly one outcome shape — matching bucket_b_pipeline.py, unlike
bucket_c_pipeline.py whose own triage produces several. No logging here;
analyze_assumptions already writes the Bucket D log entry.
Design context: adr/0019-bucket-d-analysis-and-pipeline.md.
"""

from typing import Callable

from agent_eval.bucket_d_analysis import analyze_assumptions
from agent_eval.tag_schema import ClaimTag


def run_bucket_d_pipeline(
    claim_text: str,
    *,
    company_name: str,
    claim_id: str,
    llm_fn: Callable[[str, str | None], dict] | None = None,
    log_dir: str = "logs",
) -> ClaimTag:
    """
    Run the Bucket D analysis pipeline for `claim_text`.

    Calls analyze_assumptions to surface a structured partial reading of
    the claim's reasoning, then wraps the result in a ClaimTag with
    bucket="D".

    No triage, no search, no fetch. The claim is already known to be
    Bucket D by the caller.

    Injectable fake for testing:
        llm_fn(claim_text: str, feedback: str | None) -> dict
            Returns {"assumptions": [...], "causal_steps": [...]}.

    Returns a ClaimTag with bucket="D" whose assumptions_evidence is an
    AssumptionsStatedEvidence. overall_status is "assumptions_explicit"
    if at least one assumption and one causal step are present_in_claim=True;
    "assumptions_not_stated" otherwise (including malformed LLM responses,
    which analyze_assumptions surfaces as empty lists).
    """
    evidence = analyze_assumptions(
        claim_text=claim_text,
        company_name=company_name,
        llm_fn=llm_fn,
        log_dir=log_dir,
    )
    return ClaimTag(
        claim_id=claim_id,
        claim_text=claim_text,
        bucket="D",
        assumptions_evidence=evidence,
    )
