"""
pipeline.py

Orchestration for a single Bucket A claim: wires together the three
generic, independently-tested modules (domain_check, quote_match,
tag_schema) into one verification pass.

DELIBERATE SCOPE BOUNDARY - no AI/LLM call lives here. The claim text,
source URL, and claimed quote are all supplied as direct inputs, not
extracted by a model. This keeps the whole pipeline deterministic and
testable with no API key, exactly like domain_check.py and quote_match.py.
The (future) model-driven extraction step that produces these inputs is a
separate concern, upstream of this file; mixing it in here would make the
pipeline impossible to test without a live model and would reintroduce
non-determinism into the one place that is supposed to be the trustworthy,
checkable core.

THREE FUNCTIONS, SPLIT ON PURPOSE:

1. run_bucket_a_checks - the only function that touches the actual checks.
   It needs real URLs/documents to test meaningfully.
2. build_bucket_a_tag - pure assembly: raw check results in, a ClaimTag
   out. Testable with hand-built fake inputs, no real check running.
3. verify_bucket_a_claim - the thin convenience wrapper most callers use.

Functions 1 and 2 are where the real work and the real extensibility live:
a different bucket, or a different mix of checks, is built by recombining
those two pieces. Function 3 exists only because the common case (run the
checks, then wrap them) is so common that forcing every caller to spell it
out in two steps would be noise. It is convenience, not flexibility - the
flexibility is precisely that 1 and 2 are separable, so a caller who needs
something other than the standard recipe can drop function 3 and use them
directly.
"""

from domain_check import check_domain
from quote_match import match_quote, QuoteMatchResult
from tag_schema import ClaimTag, DomainCheckEvidence, QuoteMatchEvidence


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
