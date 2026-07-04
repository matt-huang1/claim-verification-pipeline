"""Bucket A wiring: domain_check + quote_match + tag_schema in one pass.

No LLM call lives here — claim text, source URL, and quote are direct inputs,
keeping this pipeline deterministic and testable with no API key. The
model-driven step that produces these inputs is extraction.py, upstream.

Three functions, split on purpose: run_bucket_a_checks (the only function
that touches the real checks), build_bucket_a_tag (pure assembly, testable
with fakes), and verify_bucket_a_claim (the convenience recipe chaining the
two). Rationale in adr/0005-pipeline.md.
"""

from agent_eval.domain_check import check_domain
from agent_eval.quote_match import match_quote, QuoteMatchResult
from agent_eval.tag_schema import ClaimTag, DomainCheckEvidence, QuoteMatchEvidence


def run_bucket_a_checks(
    url: str, allowlist: list[str], quote: str, document: str
) -> tuple[dict, QuoteMatchResult]:
    """
    Run the two Bucket A checks and return their raw results, unwrapped.

    Returns a (domain_result, quote_result) tuple: the dict from
    check_domain and the QuoteMatchResult from match_quote, exactly as
    those functions produce them. Wrapping into evidence/tag objects is
    build_bucket_a_tag's job, kept separate so this function stays the
    single place that actually invokes the checks.
    """
    domain_result = check_domain(url, allowlist)
    quote_result = match_quote(quote, document)
    return domain_result, quote_result


def build_bucket_a_tag(
    claim_id: str,
    claim_text: str,
    domain_result: dict,
    quote_result: QuoteMatchResult,
) -> ClaimTag:
    """
    Assemble a Bucket A ClaimTag from already-computed check results.

    Pure assembly - runs no checks itself. Wraps domain_result into a
    DomainCheckEvidence and quote_result into a QuoteMatchEvidence (pulling
    the top candidate's score/text and the candidate count out of the
    QuoteMatchResult), then constructs the ClaimTag with bucket="A".

    Kept separate from run_bucket_a_checks so it can be tested with
    hand-built fake inputs, with no real domain check or quote match
    running - which is the whole point of the split.
    """
    domain_evidence = DomainCheckEvidence(
        domain=domain_result["domain"],
        passed=domain_result["passed"],
        matched_entry=domain_result["matched_entry"],
    )

    top = quote_result.candidates[0] if quote_result.candidates else None
    quote_evidence = QuoteMatchEvidence(
        status=quote_result.status,
        top_score=top.score if top is not None else None,
        matched_text=top.text if top is not None else None,
        candidate_count=len(quote_result.candidates),
    )

    return ClaimTag(
        claim_id=claim_id,
        claim_text=claim_text,
        bucket="A",
        domain_evidence=domain_evidence,
        quote_evidence=quote_evidence,
    )


def verify_bucket_a_claim(
    claim_id: str,
    claim_text: str,
    url: str,
    allowlist: list[str],
    quote: str,
    document: str,
) -> ClaimTag:
    """
    Convenience wrapper: run the checks, build the tag, return it.

    This is the "recipe" most callers want. It adds no logic of its own -
    it just chains run_bucket_a_checks into build_bucket_a_tag. A caller
    needing anything other than this standard recipe should use those two
    functions directly rather than this wrapper.
    """
    domain_result, quote_result = run_bucket_a_checks(url, allowlist, quote, document)
    return build_bucket_a_tag(claim_id, claim_text, domain_result, quote_result)
