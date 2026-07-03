"""
bucket_d_pipeline.py

Orchestrator for Bucket D: given a future-facing or counterfactual claim
text, calls analyze_assumptions and wraps the result in a ClaimTag.

WHY THERE IS NO TRIAGE STEP:

Routing to Bucket D happens upstream of this module — either the caller
identifies the claim as Bucket D and supplies bucket="D" to run_pipeline.py,
or run_pipeline.py's own triage step classifies it as bucket_d and routes
it here. Re-running triage inside this pipeline would duplicate the
dispatcher's job and could produce a contradictory routing.

WHY THE RETURN TYPE IS ClaimTag DIRECTLY, NOT A DICT:

No triage means no routing failure paths. bucket_c_pipeline.py returns a
dict because its own triage step can produce several distinct outcomes,
only one of which is a real ClaimTag. Here there is one outcome: analyze_assumptions always
returns a well-formed AssumptionsStatedEvidence (never None, never raises),
so wrapping that in a ClaimTag and returning it directly is the natural,
unambiguous shape. This matches bucket_b_pipeline.py, which also returns
ClaimTag directly because its success/failure modes are all internal to
the pipeline rather than routing decisions that change the return shape.

WHY NO ORCHESTRATOR-LEVEL LOGGING:

analyze_assumptions already writes the Bucket D log entry — one entry per
call, covering claim_text, company_name, assumptions_found,
causal_steps_found, stated counts, attempts, and outcome. Adding a second
orchestrator-level entry would duplicate that information without adding
anything, the same reasoning as bucket_c_pipeline.py (where
reconcile_sources already covers the full Bucket C outcome).
"""

from agent_eval.bucket_d_analysis import analyze_assumptions
from agent_eval.tag_schema import ClaimTag


def run_bucket_d_pipeline(
    claim_text: str,
    *,
    company_name: str,
    claim_id: str,
    llm_fn=None,
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
