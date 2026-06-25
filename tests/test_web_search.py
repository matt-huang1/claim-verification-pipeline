"""
Tests for web_search.py.

All tests mock the Brave Search API — no real HTTP calls are made. The pattern
mirrors test_page_fetch.py: mock requests.get, assert on the returned structure.

What is tested:
  - A successful API response with real result fields returns a correctly-shaped
    list of dicts with "url", "title", and "snippet" keys.
  - The max_results cap is enforced: if the API returns more results than
    requested, only max_results are returned.
  - A non-200 API response returns an empty list rather than raising.
  - A network-level exception returns an empty list rather than raising.
  - A missing BRAVE_API_KEY returns an empty list immediately (no HTTP call).
  - A malformed JSON response returns an empty list rather than raising.
  - A response with no "web" key (unexpected schema) returns an empty list.
"""

import os

import requests
from unittest.mock import MagicMock, patch

from web_search import search_for_source


def _mock_brave_response(results: list[dict], status_code: int = 200) -> MagicMock:
    """Build a mock requests.Response containing the given results list."""
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = {"web": {"results": results}}
    return mock


_BRAVE_RESULTS = [
    {
        "url": "https://pr.tsmc.com/english/news/3067",
        "title": "TSMC Accelerates RE100 Timetable",
        "description": "TSMC moves 100% renewable energy target to 2040 from 2050.",
    },
    {
        "url": "https://tsmc.com/sustainability",
        "title": "TSMC Sustainability",
        "description": "TSMC raised its 2030 target to 60 percent.",
    },
    {
        "url": "https://example.com/third",
        "title": "Third Result",
        "description": "Third snippet.",
    },
]


# --- successful responses ---


def test_successful_response_returns_shaped_results():
    mock_resp = _mock_brave_response(_BRAVE_RESULTS)
    with patch("requests.get", return_value=mock_resp):
        with patch.dict(os.environ, {"BRAVE_API_KEY": "test-key"}):
            results = search_for_source("TSMC renewable energy 2040")
    assert len(results) == 3
    assert results[0]["url"] == "https://pr.tsmc.com/english/news/3067"
    assert results[0]["title"] == "TSMC Accelerates RE100 Timetable"
    assert "2040" in results[0]["snippet"]


def test_snippet_maps_from_description_field():
    """Brave API returns 'description', which must be mapped to 'snippet'."""
    mock_resp = _mock_brave_response(
        [{"url": "https://tsmc.com", "title": "TSMC", "description": "desc text"}]
    )
    with patch("requests.get", return_value=mock_resp):
        with patch.dict(os.environ, {"BRAVE_API_KEY": "test-key"}):
            results = search_for_source("TSMC")
    assert results[0]["snippet"] == "desc text"


def test_max_results_cap_is_enforced():
    """If the API returns more results than max_results, only that many are kept."""
    mock_resp = _mock_brave_response(_BRAVE_RESULTS)  # 3 results
    with patch("requests.get", return_value=mock_resp):
        with patch.dict(os.environ, {"BRAVE_API_KEY": "test-key"}):
            results = search_for_source("TSMC", max_results=2)
    assert len(results) == 2


# --- failure cases all return empty list ---


def test_non_200_response_returns_empty_list():
    mock_resp = _mock_brave_response([], status_code=429)
    with patch("requests.get", return_value=mock_resp):
        with patch.dict(os.environ, {"BRAVE_API_KEY": "test-key"}):
            results = search_for_source("TSMC")
    assert results == []


def test_network_exception_returns_empty_list():
    with patch("requests.get", side_effect=requests.exceptions.ConnectionError):
        with patch.dict(os.environ, {"BRAVE_API_KEY": "test-key"}):
            results = search_for_source("TSMC")
    assert results == []


def test_missing_api_key_returns_empty_list_without_calling_api():
    """No HTTP call should be made when BRAVE_API_KEY is absent."""
    env = {k: v for k, v in os.environ.items() if k != "BRAVE_API_KEY"}
    with patch.dict(os.environ, env, clear=True):
        with patch("requests.get") as mock_get:
            results = search_for_source("TSMC")
    assert results == []
    mock_get.assert_not_called()


def test_malformed_json_returns_empty_list():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.side_effect = ValueError("not valid JSON")
    with patch("requests.get", return_value=mock_resp):
        with patch.dict(os.environ, {"BRAVE_API_KEY": "test-key"}):
            results = search_for_source("TSMC")
    assert results == []


def test_missing_web_key_in_response_returns_empty_list():
    """An unexpected schema (no 'web' key) must not raise."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"type": "search", "query": {}}  # no 'web' key
    with patch("requests.get", return_value=mock_resp):
        with patch.dict(os.environ, {"BRAVE_API_KEY": "test-key"}):
            results = search_for_source("TSMC")
    assert results == []
