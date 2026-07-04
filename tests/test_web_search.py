"""
Tests for web_search.py.

All tests mock the Tavily client — no real HTTP calls are made.

What is tested:
  - A successful API response returns a correctly-shaped list of dicts
    with "url", "title", and "snippet" keys.
  - Tavily's "content" field is mapped to our "snippet" key.
  - The max_results cap is enforced client-side if the API returns more.
  - Infrastructure failures raise SearchUnavailable, never return an empty
    list: a missing TAVILY_API_KEY (without constructing a client) and any
    client/API exception. An empty list is reserved for a search that ran
    and genuinely found nothing (adr/0026-search-unavailability.md).
  - A response missing the "results" key returns an empty list — the API
    responded, there just were no results to read.
  - client.search() is ALWAYS called with search_depth="basic" and
    include_raw_content=False, explicitly — enforcement of the SERP-only
    constraint documented in web_search.py's module docstring.
"""

import os

from unittest.mock import MagicMock, patch

import pytest

from agent_eval.web_search import SearchUnavailable, search_for_source

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
    with patch(
        "agent_eval.web_search.TavilyClient", return_value=_mock_client(_TAVILY_RESULTS)
    ):
        with patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}):
            results = search_for_source("TSMC renewable energy 2040")
    assert len(results) == 3
    assert results[0]["url"] == "https://pr.tsmc.com/english/news/3067"
    assert results[0]["title"] == "TSMC Accelerates RE100 Timetable"
    assert "2040" in results[0]["snippet"]


def test_snippet_maps_from_content_field():
    """Tavily returns 'content' for the snippet; it must be mapped to 'snippet'."""
    single = [{"url": "https://tsmc.com", "title": "TSMC", "content": "desc text"}]
    with patch("agent_eval.web_search.TavilyClient", return_value=_mock_client(single)):
        with patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}):
            results = search_for_source("TSMC")
    assert results[0]["snippet"] == "desc text"


def test_max_results_cap_is_enforced():
    """If the API returns more results than max_results, only that many are kept."""
    with patch(
        "agent_eval.web_search.TavilyClient", return_value=_mock_client(_TAVILY_RESULTS)
    ):
        with patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}):
            results = search_for_source("TSMC", max_results=2)
    assert len(results) == 2


# --- infrastructure failures raise SearchUnavailable ---


def test_network_exception_raises_search_unavailable():
    """An API/client failure is an infrastructure failure, not "no results"."""
    mock_client = MagicMock()
    mock_client.search.side_effect = Exception("connection failed")
    with patch("agent_eval.web_search.TavilyClient", return_value=mock_client):
        with patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}):
            with pytest.raises(SearchUnavailable):
                search_for_source("TSMC")


def test_missing_api_key_raises_search_unavailable_without_calling_api():
    """No client should be constructed when TAVILY_API_KEY is absent, and the
    failure must be named — a missing key returning [] would let a config
    error masquerade as an honest "no sources for this claim" outcome."""
    env = {k: v for k, v in os.environ.items() if k != "TAVILY_API_KEY"}
    with patch.dict(os.environ, env, clear=True):
        with patch("agent_eval.web_search.TavilyClient") as mock_cls:
            with pytest.raises(SearchUnavailable):
                search_for_source("TSMC")
    mock_cls.assert_not_called()


def test_client_valueerror_raises_search_unavailable():
    """Any exception from the client is wrapped, preserving the cause."""
    mock_client = MagicMock()
    mock_client.search.side_effect = ValueError("unexpected response format")
    with patch("agent_eval.web_search.TavilyClient", return_value=mock_client):
        with patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"}):
            with pytest.raises(SearchUnavailable) as exc_info:
                search_for_source("TSMC")
    assert isinstance(exc_info.value.__cause__, ValueError)


# --- a response that arrived but holds nothing is an empty list ---


def test_missing_results_key_returns_empty_list():
    """A response with no 'results' key means the API answered with nothing
    to read — a genuine empty result set, not unavailability."""
    mock_client = MagicMock()
    mock_client.search.return_value = {"query": "TSMC"}  # no 'results' key
    with patch("agent_eval.web_search.TavilyClient", return_value=mock_client):
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
    with patch("agent_eval.web_search.TavilyClient", return_value=mock_client):
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
