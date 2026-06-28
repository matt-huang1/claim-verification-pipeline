"""
Tests for tpi_extract.py.

All deterministic tests mock requests.get — no real HTTP calls. One live
test (marked live_api) calls the real TPI page for TotalEnergies and asserts
the known result: indicators 21 and 22 are "no", all others are "yes".
That known result was independently cross-checked against an RBC analyst
document before this module was written.
"""

import os

import pytest
import requests
from unittest.mock import MagicMock, patch

from tpi_extract import extract_tpi_management_quality

# ---------------------------------------------------------------------------
# HTML fixture helpers
# ---------------------------------------------------------------------------


def _build_html(indicators: list[str], include_level: bool = True) -> str:
    """
    Build a minimal TPI-style HTML page with the given indicator pass/fail list.
    Each string in `indicators` must be "yes" or "no".
    """
    divs = "\n".join(
        f'<div class="mq-answer mq-answer--{v} level{(i % 6)}">'
        f"indicator {i + 1}</div>"
        for i, v in enumerate(indicators)
    )
    level_text = "<p>Current level: 4</p>" if include_level else ""
    return f"<html><body>{level_text}{divs}</body></html>"


def _mock_response(html: str, status_code: int = 200) -> MagicMock:
    mock = MagicMock()
    mock.status_code = status_code
    mock.content = html.encode("utf-8")
    return mock


# ---------------------------------------------------------------------------
# Deterministic tests
# ---------------------------------------------------------------------------


def _totalenergies_indicators() -> list[str]:
    """23 indicators with 21 and 22 as "no", all others "yes"."""
    return ["yes" if i not in (20, 21) else "no" for i in range(23)]


def test_parse_confirmed_totalenergies_structure():
    """
    Parse a mocked HTML matching the confirmed TotalEnergies structure:
    23 indicators, indicators 21 and 22 are "no", all others "yes".
    """
    html = _build_html(_totalenergies_indicators())
    with patch("tpi_extract.requests.get", return_value=_mock_response(html)):
        result = extract_tpi_management_quality("totalenergies")

    assert result["success"] is True
    assert result["failure_reason"] is None
    assert result["overall_level"] == 4

    indicators = result["indicators"]
    assert set(indicators.keys()) == set(range(1, 24))
    assert indicators[21] == "no"
    assert indicators[22] == "no"
    for i in range(1, 24):
        if i not in (21, 22):
            assert indicators[i] == "yes", f"indicator {i} should be yes"


def test_fetch_failure_network_error():
    with patch(
        "tpi_extract.requests.get",
        side_effect=requests.exceptions.ConnectionError("unreachable"),
    ):
        result = extract_tpi_management_quality("somecompany")

    assert result["success"] is False
    assert result["failure_reason"] == "fetch_failed"
    assert result["overall_level"] is None
    assert result["indicators"] is None


def test_fetch_failure_timeout():
    with patch(
        "tpi_extract.requests.get",
        side_effect=requests.exceptions.Timeout(),
    ):
        result = extract_tpi_management_quality("somecompany")

    assert result["success"] is False
    assert result["failure_reason"] == "fetch_failed"


def test_fetch_failure_non_200():
    with patch(
        "tpi_extract.requests.get",
        return_value=_mock_response("<html></html>", status_code=404),
    ):
        result = extract_tpi_management_quality("somecompany")

    assert result["success"] is False
    assert result["failure_reason"] == "fetch_failed"


def test_wrong_indicator_count_returns_unexpected_indicator_count():
    """20 indicators instead of 23 — refuse to guess, don't truncate/pad."""
    html = _build_html(["yes"] * 20)
    with patch("tpi_extract.requests.get", return_value=_mock_response(html)):
        result = extract_tpi_management_quality("somecompany")

    assert result["success"] is False
    assert result["failure_reason"] == "unexpected_indicator_count"
    assert result["indicators"] is None


def test_unexpected_class_value_returns_unexpected_indicator_value():
    """
    A div with class "mq-answer level3" (no --yes or --no) triggers
    unexpected_indicator_value, not a silent parse miss.
    """
    # Build HTML manually so we can inject a malformed div alongside 22 normal ones
    divs_good = "\n".join(
        '<div class="mq-answer mq-answer--yes level1">x</div>' for _ in range(22)
    )
    bad_div = '<div class="mq-answer level3">weird</div>'
    html = f"<html><body>{divs_good}{bad_div}</body></html>"

    with patch("tpi_extract.requests.get", return_value=_mock_response(html)):
        result = extract_tpi_management_quality("somecompany")

    assert result["success"] is False
    assert result["failure_reason"] == "unexpected_indicator_value"


def test_overall_level_none_when_not_in_page():
    html = _build_html(_totalenergies_indicators(), include_level=False)
    with patch("tpi_extract.requests.get", return_value=_mock_response(html)):
        result = extract_tpi_management_quality("totalenergies")

    assert result["success"] is True
    assert result["overall_level"] is None


# ---------------------------------------------------------------------------
# Live test
# ---------------------------------------------------------------------------


@pytest.mark.live_api
@pytest.mark.skipif(
    not os.getenv("RUN_LIVE_API"),
    reason="live API test; set RUN_LIVE_API=1 to run deliberately",
)
def test_live_totalenergies_indicators_21_and_22_are_no():
    """
    Fetch the real TPI page for TotalEnergies and assert the known result:
    indicators 21 and 22 are "no", all others are "yes".

    Known result cross-checked against an independent RBC analyst document
    that stated TotalEnergies fails indicators 21 and 22 only. The HTML parse
    of the live page matched exactly before this test was written.
    """
    result = extract_tpi_management_quality("totalenergies")

    assert result["success"] is True, f"Live fetch failed: {result['failure_reason']}"

    indicators = result["indicators"]
    assert indicators[21] == "no", "indicator 21 should be no (known fail)"
    assert indicators[22] == "no", "indicator 22 should be no (known fail)"
    for i in range(1, 24):
        if i not in (21, 22):
            assert indicators[i] == "yes", f"indicator {i} should be yes"
