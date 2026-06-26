"""
web_search.py

Proposes candidate source URLs for a search query using the Tavily Search API.
This module knows nothing about claims, companies, buckets, or the extraction
loop — it takes a query string and returns raw search results, for the same
reason domain_check.py and quote_match.py are generic: the same function
should work for any query, regardless of domain.

WHY TAVILY OVER ALTERNATIVES:

  Brave Search: was the prior provider. Brave removed its free tier in
  February 2026 — it now requires a credit card and metered credits at
  $5/month minimum. Replaced on that basis.

  SerpApi: free tier is capped at 100 searches/month with a credit card
  required, and its results are scraped from Google, which carries legal
  exposure this project wants to avoid.

  Tavily: 1,000 credits/month free tier, no credit card required. The
  free tier is ongoing, not a signup bonus. This matches the project's
  low-volume usage without adding cost or requiring payment information.

  gpt-4o-search-preview / Responses API: rejected for the same reasons as
  before — bundled AI content extraction this pipeline does not need
  (page_fetch.py already handles it), and per-search pricing (~$30-50 per
  1,000 searches) that is 100x more expensive than a plain SERP API.

CRITICAL DESIGN CONSTRAINT — SERP ONLY, NO EXTRACT:

Tavily bundles search and full-content extraction as a paired product. Its
documentation actively presents "search then extract" (calling both
/search and /extract) as the standard pattern, and offers search_depth=
"advanced" to pull richer content during search itself. This project must
NOT follow that pattern.

This module calls ONLY Tavily's /search endpoint, with:
  - search_depth="basic" (always, explicitly, never "advanced" or "fast")
  - include_raw_content=False (always, explicitly — omitting this field
    would risk a future Tavily default change silently returning extracted
    content)

The /extract endpoint is never called from this module. There is no code
path here that could reach it. This constraint is enforced in tests, not
just stated in comments: see test_search_depth_and_raw_content_are_explicitly_set.

WHY THIS MATTERS:

page_fetch.py is the only module in this project responsible for content
extraction. It fetches real page text after a URL has been independently
confirmed legitimate (domain check + url_compare match against search
results). Calling Tavily extract or advanced-depth search would:
  1. Pay for content extraction capability already built, tested, and
     trusted in page_fetch.py — the same reasoning that ruled out
     gpt-4o-search-preview's bundled extraction earlier.
  2. Architecturally bypass the separation that makes this pipeline
     auditable: if content comes from Tavily's extraction layer rather
     than from a URL the pipeline independently verified and fetched, the
     content is no longer from a source this pipeline can vouch for.

ON FAILURE, RETURN EMPTY — do not raise:

Any failure (missing API key, network error, malformed response, missing
"results" key) returns an empty list rather than raising an exception. The
caller reacts to "no candidates found" as a named failure state within its
own retry logic; it does not need to catch exceptions from this module.
This matches the "report honestly, don't retry" design of page_fetch.py.
"""

import os

from tavily import TavilyClient


def search_for_source(query: str, max_results: int = 5) -> list[dict]:
    """
    Search for candidate source URLs using the Tavily Search API.

    Returns a list of results, each with "url", "title", and "snippet" keys.
    Returns an empty list on any failure — never raises. See module docstring.

    Calls ONLY the basic /search endpoint. search_depth="basic" and
    include_raw_content=False are set explicitly on every call — see module
    docstring for why this constraint exists and why it is explicit rather
    than relying on defaults.

    Args:
        query:       The search query. Caller decides how to construct it;
                     this function treats it as an opaque string.
        max_results: Maximum number of results to return. Default 5.

    Requires TAVILY_API_KEY in the environment (loaded via python-dotenv).
    Returns an empty list immediately if the key is absent.
    """
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return []

    try:
        client = TavilyClient(api_key=api_key)
        response = client.search(
            query,
            search_depth="basic",
            max_results=max_results,
            include_raw_content=False,
        )
        raw = response.get("results", [])
        return [
            {
                "url": r.get("url", ""),
                "title": r.get("title", ""),
                "snippet": r.get("content", ""),
            }
            for r in raw[:max_results]
        ]
    except Exception:
        return []
