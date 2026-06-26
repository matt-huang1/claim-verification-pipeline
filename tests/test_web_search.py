"""
Tests for web_search.py.

All tests mock the Tavily client — no real HTTP calls are made.

What is tested:
  - A successful API response returns a correctly-shaped list of dicts
    with "url", "title", and "snippet" keys.
  - Tavily's "content" field is mapped to our "snippet" key.
  - The max_results cap is enforced client-side if the API returns more.
  - A network-level exception returns an empty list rather than raising.
  - A missing TAVILY_API_KEY returns an empty list without making any call.
  - A malformed/missing "results" key returns an empty list rather than raising.
  - client.search() is ALWAYS called with search_depth="basic" and
    include_raw_content=False, explicitly — enforcement of the SERP-only
    constraint documented in web_search.py's module docstring.
"""

import os

from unittest.mock import MagicMock, patch

from web_search import search_for_source

_TAVILY_RESULTS = [
    {
        "url": "https://pr.tsmc.com/english/news/3067",
        "title": "TSMC Accelerates RE100 Timetable",
        "content": "TSMC moves 100% renewable energy target to 2040 from 2050.",
    },
    {
        "url": "https://tsmc.com/sustainability",
        "title": "TSMC Sustainability",
        "content": "TSMC raised its 2030 target to 60 percent.",
    },
    {
        "url": "https://example.com/third",
        "title": "Third Result",
        "content": "Third snippet.",
    },
]


def _mock_client(results: list[dict]) -> MagicMock:
    """Build a mock TavilyClient whose .search() returns the given results."""
    mock = MagicMock()
    mock.search.return_value = {"results": results}
    return mock


# --- successful responses ---


def test_successful_response_returns_shaped_results():
    with patch("web_search.TavilyClient", return_value=_mock_client(_TAVILY_RESULTS)):
        with patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}):
            results = search_for_source("TSMC renewable energy 2040")
    assert len(results) == 3
    assert results[0]["url"] == "https://pr.tsmc.com/english/news/3067"
    assert results[0]["title"] == "TSMC Accelerates RE100 Timetable"
    assert "2040" in results[0]["snippet"]


def test_snippet_maps_from_content_field():
    """Tavily returns 'content' for the snippet; it must be mapped to 'snippet'."""
    single = [{"url": "https://tsmc.com", "title": "TSMC", "content": "desc text"}]
    with patch("web_search.TavilyClient", return_value=_mock_client(single)):
        with patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}):
            results = search_for_source("TSMC")
    assert results[0]["snippet"] == "desc text"


def test_max_results_cap_is_enforced():
    """If the API returns more results than max_results, only that many are kept."""
    with patch("web_search.TavilyClient", return_value=_mock_client(_TAVILY_RESULTS)):
        with patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}):
            results = search_for_source("TSMC", max_results=2)
    assert len(results) == 2


# --- failure cases all return empty list ---


def test_network_exception_returns_empty_list():
    mock_client = MagicMock()
    mock_client.search.side_effect = Exception("connection failed")
    with patch("web_search.TavilyClient", return_value=mock_client):
        with patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}):
            results = search_for_source("TSMC")
    assert results == []


def test_missing_api_key_returns_empty_list_without_calling_api():
    """No client should be constructed when TAVILY_API_KEY is absent."""
    env = {k: v for k, v in os.environ.items() if k != "TAVILY_API_KEY"}
    with patch.dict(os.environ, env, clear=True):
        with patch("web_search.TavilyClient") as mock_cls:
            results = search_for_source("TSMC")
    assert results == []
    mock_cls.assert_not_called()


def test_missing_results_key_returns_empty_list():
    """An unexpected response schema (no 'results' key) must not raise."""
    mock_client = MagicMock()
    mock_client.search.return_value = {"query": "TSMC"}  # no 'results' key
    with patch("web_search.TavilyClient", return_value=mock_client):
        with patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}):
            results = search_for_source("TSMC")
    assert results == []


def test_malformed_response_returns_empty_list():
    """A non-dict response (e.g. API client raises) must return empty, not raise."""
    mock_client = MagicMock()
    mock_client.search.side_effect = ValueError("unexpected response format")
    with patch("web_search.TavilyClient", return_value=mock_client):
        with patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}):
            results = search_for_source("TSMC")
    assert results == []


# --- SERP-only constraint enforcement ---


def test_search_depth_and_raw_content_are_explicitly_set():
    """
    Every call to client.search() must pass search_depth='basic' and
    include_raw_content=False explicitly. This is the test that would catch
    a future accidental change toward advanced or extracted content —
    the actual enforcement mechanism for the SERP-only design constraint
    documented in web_search.py's module docstring.
    """
    mock_client = _mock_client(_TAVILY_RESULTS)
    with patch("web_search.TavilyClient", return_value=mock_client):
        with patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}):
            search_for_source("TSMC renewable energy 2040", max_results=3)

    mock_client.search.assert_called_once()
    _, kwargs = mock_client.search.call_args
    assert (
        kwargs.get("search_depth") == "basic"
    ), f"search_depth must be 'basic', got {kwargs.get('search_depth')!r}"
    assert (
        kwargs.get("include_raw_content") is False
    ), f"include_raw_content must be False, got {kwargs.get('include_raw_content')!r}"
