"""Tavily search wrapper — SERP only, generic, unavailability is named.

Takes a query string, returns raw search results. Knows nothing about claims,
companies, or the extraction loop.

Load-bearing constraint — SERP only, no extract: this module calls ONLY
Tavily's /search endpoint, always with search_depth="basic" and
include_raw_content=False set explicitly. page_fetch.py is the only module
responsible for content extraction; content arriving through Tavily's
extraction layer instead of from a URL the pipeline independently verified
and fetched would bypass the separation that makes the pipeline auditable.
The constraint is enforced by a test
(test_search_depth_and_raw_content_are_explicitly_set), not just stated here.

Two different worlds must never look the same to a caller (the "failures
are named, never collapsed" principle, applied to this module's own
contract — adr/0026-search-unavailability.md):

- The search ran and genuinely found nothing → an empty list. A retryable,
  claim-specific state the caller handles in its own loop.
- The search could not run at all (missing API key, auth/quota failure,
  network error) → SearchUnavailable is raised. A configuration or
  infrastructure failure that no retry of the same claim will fix, and
  that must never masquerade as an honest "no results for this claim".

Provider selection history (Brave's withdrawn free tier, SerpApi, bundled
LLM search) is recorded in adr/0006-extraction.md.
"""

import os
from typing import TypedDict

from tavily import TavilyClient


class SearchUnavailable(Exception):
    """Search infrastructure could not be used at all.

    Raised for a missing TAVILY_API_KEY or any client/API failure (auth,
    quota, network). Deliberately distinct from an empty result list, which
    means the search ran and genuinely found nothing — collapsing the two
    would let a configuration error masquerade as an honest verification
    outcome. See module docstring and adr/0026-search-unavailability.md.
    """


class SearchResult(TypedDict):
    """One search candidate as every downstream module consumes it."""

    url: str
    title: str
    snippet: str


def search_for_source(query: str, max_results: int = 5) -> list[SearchResult]:
    """
    Search for candidate source URLs using the Tavily Search API.

    Returns a list of results, each with "url", "title", and "snippet" keys.
    An empty list means the search ran and found nothing. If the search
    could not run at all (no TAVILY_API_KEY, or any client/API failure),
    raises SearchUnavailable — see module docstring for why these two
    states are never collapsed.

    Calls ONLY the basic /search endpoint. search_depth="basic" and
    include_raw_content=False are set explicitly on every call — see module
    docstring for why this constraint exists and why it is explicit rather
    than relying on defaults.

    Args:
        query:       The search query. Caller decides how to construct it;
                     this function treats it as an opaque string.
        max_results: Maximum number of results to return. Default 5.

    Requires TAVILY_API_KEY in the environment (loaded via python-dotenv).
    """
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        raise SearchUnavailable("TAVILY_API_KEY is not set")

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
    except Exception as exc:
        raise SearchUnavailable(f"search call failed: {exc}") from exc
