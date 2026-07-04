"""Verification layer for AI-assisted climate transition research claims.

Public API. The single entry point most callers want is run_pipeline; the
deterministic checks (check_domain, match_quote, same_url) and the ClaimTag
schema are exported for callers composing their own verification recipes.
"""

from agent_eval.domain_check import check_domain
from agent_eval.pipeline import verify_bucket_a_claim
from agent_eval.quote_match import QuoteMatchResult, match_quote
from agent_eval.run_pipeline import run_pipeline
from agent_eval.serialisation import (
    dict_to_result,
    dict_to_tag,
    result_to_dict,
    tag_to_dict,
)
from agent_eval.tag_schema import ClaimTag
from agent_eval.url_compare import same_url

__version__ = "0.1.0"

__all__ = [
    "ClaimTag",
    "QuoteMatchResult",
    "check_domain",
    "dict_to_result",
    "dict_to_tag",
    "match_quote",
    "result_to_dict",
    "run_pipeline",
    "same_url",
    "tag_to_dict",
    "verify_bucket_a_claim",
    "__version__",
]
