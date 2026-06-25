"""
web_search.py

Proposes candidate source URLs for a search query using the Brave Search API.
This module knows nothing about claims, companies, buckets, or the extraction
loop — it takes a query string and returns raw search results, for the same
reason domain_check.py and quote_match.py are generic: the same function
should work for any query, regardless of domain.

WHY BRAVE SEARCH OVER OPENAI'S BUNDLED SEARCH OPTIONS:

Two OpenAI-native alternatives were evaluated and rejected before choosing
Brave:

  Responses API (with built-in web_search tool): lets the model decide whether
  to search. This duplicates a decision already made better elsewhere in this
  codebase: is_verifiable_claim() in extraction.py determines whether a claim
  is worth attempting, deterministically and at zero cost, before any API call
  is made. The Responses API's main advantage is the model-decides-to-search
  capability — but that decision is already handled, so paying for it would
  mean paying for a capability this project deliberately avoids.

  gpt-4o-search-preview (Chat Completions): priced per-search (~$30-50 per
  1,000 searches), not per token — roughly 100x more expensive than a plain
  SERP API for the same list of URLs. It also bundles AI content-extraction
  capability this pipeline does not need (page_fetch.py already handles
  content retrieval). Paying for that bundle would mean paying twice for
  a capability already built and tested.

Brave was chosen because it is the right SHAPE for this architecture: it
returns URLs, titles, and snippets only — no content extraction, no AI
reasoning bundled in. The existing modules (check_domain, page_fetch,
match_quote) handle everything downstream of URL discovery. The free tier
(2,000 queries/month, ongoing — not a one-time signup bonus) matches this
project's low-volume usage, meaning search adds capability without adding
cost at current scale.

ON FAILURE, RETURN EMPTY — do not raise:

Any failure (missing API key, network error, non-200 response, malformed
JSON) returns an empty list rather than raising an exception. The caller
reacts to "no candidates found" as a named failure state within its own
retry logic; it does not need to catch exceptions from this module. This
matches the "report honestly, don't retry" design of page_fetch.py.
"""

import os

import requests

BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"


def search_for_source(query: str, max_results: int = 5) -> list[dict]:
    """
    Search for candidate source URLs using the Brave Search API.

    Returns a list of results, each with "url", "title", and "snippet" keys.
    Returns an empty list on any failure — never raises. See module docstring.

    Args:
        query:       The search query. Caller decides how to construct it;
                     this function treats it as an opaque string.
        max_results: Maximum number of results to return. Default 5.

    Requires BRAVE_API_KEY in the environment (loaded via python-dotenv).
    Returns an empty list immediately if the key is absent.
    """
    api_key = os.getenv("BRAVE_API_KEY")
    if not api_key:
        return []

    try:
        response = requests.get(
            BRAVE_SEARCH_URL,
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": api_key,
            },
            params={"q": query, "count": max_results},
            timeout=10,
        )
        if response.status_code != 200:
            return []
        data = response.json()
        raw = data.get("web", {}).get("results", [])
        return [
            {
                "url": r.get("url", ""),
                "title": r.get("title", ""),
                "snippet": r.get("description", ""),
            }
            for r in raw[:max_results]
        ]
    except Exception:
        return []
