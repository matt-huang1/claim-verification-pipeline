"""
Tests for tpi_extract.py.

All deterministic tests mock requests.get — no real HTTP calls. Live tests
(marked live_api + skipif) call real TPI endpoints and assert known results
cross-checked against independent sources before being written.
"""

import json
import os

import pytest
import requests
from unittest.mock import MagicMock, patch

from tpi_extract import extract_tpi_management_quality, _parse_chart_response

# ---------------------------------------------------------------------------
# HTML fixture helpers
# ---------------------------------------------------------------------------

_FAKE_DROPDOWN_OPTIONS = [
    {"label": "15/12/2025", "value": 9999},
    {"label": "15/12/2024", "value": 9998},
]
_FAKE_COMPANY_ID = "1216"


def _dropdown_div(company_id: str = _FAKE_COMPANY_ID, options=None) -> str:
    """
    Build a <div data-react-class="RemoteDropdown"> snippet matching the
    confirmed real page structure. The data-react-props JSON is HTML-entity
    encoded, as it appears in real TPI HTML.
    """
    if options is None:
        options = _FAKE_DROPDOWN_OPTIONS
    props = json.dumps(
        {
            "name": "mq_assessment_id",
            "remote": True,
            "url": f"/companies/{company_id}/mq_assessment",
            "data": options,
        }
    )
    # Encode the JSON as HTML entities the same way a browser would receive it.
    encoded = props.replace("&", "&amp;").replace('"', "&quot;")
    return f'<div data-react-class="RemoteDropdown" data-react-props="{encoded}"></div>'


def _build_html(
    indicators: list[str],
    include_level: bool = True,
    include_dropdown: bool = False,
) -> str:
    """
    Build a minimal TPI-style HTML page with the given indicator pass/fail list.
    Each string in `indicators` must be "yes" or "no".
    include_dropdown=True adds a RemoteDropdown div with a fake company ID.
    """
    divs = "\n".join(
        f'<div class="mq-answer mq-answer--{v} level{(i % 6)}">'
        f"indicator {i + 1}</div>"
        for i, v in enumerate(indicators)
    )
    level_text = "<p>Current level: 4</p>" if include_level else ""
    dropdown = _dropdown_div() if include_dropdown else ""
    return f"<html><body>{level_text}{dropdown}{divs}</body></html>"


def _mock_response(
    body: bytes | str, status_code: int = 200, content_type: str = "text/html"
) -> MagicMock:
    mock = MagicMock()
    mock.status_code = status_code
    if isinstance(body, str):
        body = body.encode("utf-8")
    mock.content = body
    return mock


def _mock_json_response(data, status_code: int = 200) -> MagicMock:
    return _mock_response(json.dumps(data).encode("utf-8"), status_code=status_code)


def _fake_chart_data() -> list:
    return [
        {
            "name": "Level",
            "data": [["01/07/2017", 3], ["01/07/2018", 4], ["15/12/2024", 5]],
        },
        {"name": "Current Level", "data": [["15/12/2025", 5]]},
        {"name": "Max Level", "data": 5},
    ]


# ---------------------------------------------------------------------------
# Deterministic tests — indicator parsing (unchanged behaviour)
# ---------------------------------------------------------------------------


def _totalenergies_indicators() -> list[str]:
    """23 indicators with 21 and 22 as "no", all others "yes"."""
    return ["yes" if i not in (20, 21) else "no" for i in range(23)]


def test_parse_confirmed_totalenergies_structure():
    """
    Parse a mocked HTML matching the confirmed TotalEnergies structure:
    23 indicators, indicators 21 and 22 are "no", all others "yes".
    No dropdown in fixture — historical_levels is None (no attempt made).
    """
    html = _build_html(_totalenergies_indicators())
    with patch("tpi_extract.requests.get", return_value=_mock_response(html)):
        result = extract_tpi_management_quality("totalenergies")

    assert result["success"] is True
    assert result["failure_reason"] is None
    assert result["overall_level"] == 4
    assert result["historical_levels"] is None
    assert result["historical_fetch_failure_reason"] is None

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
    assert result["historical_levels"] is None


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
# Deterministic tests — dropdown JSON parsing
# ---------------------------------------------------------------------------


def test_dropdown_options_parse_date_and_id_correctly():
    """
    A RemoteDropdown div with HTML-entity-encoded JSON produces correctly
    decoded (date_str, assessment_id) pairs.
    """
    options = [
        {"label": "15/12/2025", "value": 1001},
        {"label": "15/12/2024", "value": 1000},
        {"label": "01/07/2017", "value": 900},
    ]
    html = _build_html(
        _totalenergies_indicators(),
        include_dropdown=False,
    )
    # Inject a dropdown with specific options directly via _dropdown_div.
    html_with_dropdown = html.replace(
        "<html><body>",
        f"<html><body>{_dropdown_div(company_id='42', options=options)}",
    )
    with patch(
        "tpi_extract.requests.get",
        side_effect=[
            _mock_response(html_with_dropdown),
            _mock_json_response(_fake_chart_data()),
        ],
    ):
        result = extract_tpi_management_quality("somecompany")

    assert result["success"] is True
    # Company ID extracted from dropdown URL "/companies/42/mq_assessment"
    assert result["historical_levels"] is not None


# ---------------------------------------------------------------------------
# Deterministic tests — chart data parsing
# ---------------------------------------------------------------------------


def test_parse_chart_response_extracts_level_series_and_max_level():
    """
    _parse_chart_response correctly maps the "Level" series to
    (date_str, int) tuples, reads current_level_entry from "Current Level",
    and reads max_level from "Max Level".
    """
    data = _fake_chart_data()
    historical, current_entry, max_level = _parse_chart_response(data)

    assert historical == [("01/07/2017", 3), ("01/07/2018", 4), ("15/12/2024", 5)]
    assert current_entry == ("15/12/2025", 5)
    assert max_level == 5


def test_chart_data_fetched_and_parsed_end_to_end():
    """
    Full pipeline with dropdown + valid chart response: historical_levels and
    max_level are populated in the result.
    """
    html = _build_html(_totalenergies_indicators(), include_dropdown=True)
    with patch(
        "tpi_extract.requests.get",
        side_effect=[
            _mock_response(html),
            _mock_json_response(_fake_chart_data()),
        ],
    ):
        result = extract_tpi_management_quality("totalenergies")

    assert result["success"] is True
    assert result["failure_reason"] is None
    assert result["historical_levels"] == [
        ("01/07/2017", 3),
        ("01/07/2018", 4),
        ("15/12/2024", 5),
    ]
    assert result["current_level_date"] == "15/12/2025"
    assert result["max_level"] == 5
    assert result["historical_fetch_failure_reason"] is None


def test_missing_current_level_series_does_not_affect_historical_or_max():
    """
    A chart response with no "Current Level" entry returns current_level_date=None
    without disturbing historical_levels or max_level — failing precisely, not
    uniformly.
    """
    chart_without_current = [
        s for s in _fake_chart_data() if s["name"] != "Current Level"
    ]
    html = _build_html(_totalenergies_indicators(), include_dropdown=True)
    with patch(
        "tpi_extract.requests.get",
        side_effect=[
            _mock_response(html),
            _mock_json_response(chart_without_current),
        ],
    ):
        result = extract_tpi_management_quality("totalenergies")

    assert result["success"] is True
    assert result["historical_levels"] == [
        ("01/07/2017", 3),
        ("01/07/2018", 4),
        ("15/12/2024", 5),
    ]
    assert result["current_level_date"] is None
    assert result["max_level"] == 5


# ---------------------------------------------------------------------------
# Deterministic test — fail precisely, not uniformly
# ---------------------------------------------------------------------------


def test_historical_fetch_failure_preserves_indicator_results():
    """
    When the second (chart data) fetch fails, success=True and indicator
    results are intact. Only historical_levels is None and
    historical_fetch_failure_reason is populated.

    This test proves the "fail precisely, not uniformly" design: a failure
    in the supplementary historical fetch does not discard a successful
    indicator parse.
    """
    html = _build_html(_totalenergies_indicators(), include_dropdown=True)
    with patch(
        "tpi_extract.requests.get",
        side_effect=[
            _mock_response(html),
            _mock_response(b"", status_code=503),
        ],
    ):
        result = extract_tpi_management_quality("totalenergies")

    assert result["success"] is True
    assert result["failure_reason"] is None
    # Indicator data preserved
    assert result["indicators"] is not None
    assert result["indicators"][21] == "no"
    assert result["indicators"][22] == "no"
    # Historical data absent, with precise reason
    assert result["historical_levels"] is None
    assert result["historical_fetch_failure_reason"] == "historical_fetch_failed"


# ---------------------------------------------------------------------------
# Live tests
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


@pytest.mark.live_api
@pytest.mark.skipif(
    not os.getenv("RUN_LIVE_API"),
    reason="live API test; set RUN_LIVE_API=1 to run deliberately",
)
def test_live_totalenergies_historical_levels():
    """
    Fetch the real TPI historical chart data for TotalEnergies and assert
    known values: 2017 assessment is level 3, a 2024 assessment reaches
    level 5, and max_level is 5.

    These data points were read directly from the real chart endpoint before
    this test was written. Cross-checked against the same RBC document that
    describes TotalEnergies as a TPI Level 5 company.
    """
    result = extract_tpi_management_quality("totalenergies")

    assert result["success"] is True, f"Live fetch failed: {result['failure_reason']}"
    assert (
        result["historical_fetch_failure_reason"] is None
    ), f"Historical fetch failed: {result['historical_fetch_failure_reason']}"

    historical = result["historical_levels"]
    assert historical is not None
    levels_by_date = dict(historical)

    assert levels_by_date.get("01/07/2017") == 3, "2017 assessment should be level 3"

    # First level-5 entry should be in a 2024 assessment date.
    level_5_dates = [d for d, lv in historical if lv == 5]
    assert any("2024" in d for d in level_5_dates), "expected a 2024 entry at level 5"

    assert result["max_level"] == 5
    assert result["current_level_date"] == "15/12/2025"
