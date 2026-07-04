"""Tavily search wrapper — SERP only, generic, never raises.

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

Any failure returns an empty list rather than raising — the caller treats
"no candidates" as a named state in its own retry logic.

Provider selection history (Brave's withdrawn free tier, SerpApi, bundled
LLM search) is recorded in adr/0006-extraction.md.
"""

import os
from typing import TypedDict

from tavily import TavilyClient


class SearchResult(TypedDict):
    """One search candidate as every downstream module consumes it."""

    url: str
    title: str
    snippet: str


def search_for_source(query: str, max_results: int = 5) -> list[SearchResult]:
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
