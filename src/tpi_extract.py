"""
tpi_extract.py

Extracts TPI (Transition Pathway Initiative) Management Quality assessment
data from a company's TPI profile page by parsing the raw HTML.

WHY RAW HTML, NOT page_fetch.py:

page_fetch.py converts HTML to plain text — it strips all tags and their
attributes. The Management Quality indicator pass/fail state is encoded
exclusively in CSS class attributes on <div> elements
("mq-answer mq-answer--yes levelN" or "mq-answer mq-answer--no levelN").
Plain-text extraction destroys this information entirely before any parser
can read it. This module fetches raw HTML with requests directly and builds
a dedicated HTMLParser subclass that reads only the class attributes, leaving
everything else aside.

CONFIRMED PAGE STRUCTURE (as of 2026-06-28, TotalEnergies):

Each of the 23 Management Quality indicators is rendered as a div with class:

    "mq-answer mq-answer--yes levelN"   (indicator passed)
    "mq-answer mq-answer--no levelN"    (indicator failed)

where N is an integer (0–5) representing the TPI level the indicator belongs
to. The divs appear in document order, matching indicator numbers 1 through 23.
Cross-checked against an independent RBC analyst document that independently
stated TotalEnergies fails indicators 21 and 22 only — the HTML parse matched
exactly.

The same page also contains a RemoteDropdown React component encoded as a
<div data-react-class="RemoteDropdown" data-react-props="..."> element. The
data-react-props attribute is an HTML-entity-encoded JSON object of the form:

    {"name": "mq_assessment_id", "remote": true,
     "url": "/companies/1216/mq_assessment",
     "data": [{"label": "15 December 2025", "value": 13671}, ...]}

The "data" list is the full historical assessment record — one entry per
past assessment date (in "DD Month YYYY" format). The "url" field contains
the company's numeric ID.

THIS STRUCTURE IS CONFIRMED FOR EXACTLY ONE COMPANY'S PAGE (TotalEnergies).
It is not assumed universal. A different company's page may have a different
number of applicable indicators, or TPI may change their page template. The
function treats any unexpected indicator count or class value as an honest
failure rather than silently guessing — the same discipline applied in
page_fetch.py for unexpected content types and in extraction.py for malformed
LLM responses.

HISTORICAL TREND DATA — SECOND ENDPOINT (confirmed 2026-06-28):

    https://www.transitionpathwayinitiative.org/companies/{company_id}/
        assessments_levels_chart_data?mq_assessment_id={id}

Returns JSON:

    [
      {"name": "Level", "data": [["01/07/2017", 3], ["01/07/2018", 4], ...]},
      {"name": "Current Level", "data": [[...]]},
      {"name": "Max Level", "data": 5}
    ]

The company_id is extracted from the RemoteDropdown "url" field (already
present in the first fetch's HTML — no additional caller-supplied parameter
needed). Any valid mq_assessment_id from the dropdown options list is
sufficient to retrieve the full historical Level series.

HONEST FAILURE DESIGN — TWO-PHASE, FAIL PRECISELY:

The function makes two HTTP requests. If the second (historical) fetch fails,
the indicator results from the first fetch are not discarded. The same
principle as page_fetch.py: fail at the granularity of the specific thing
that failed, not the whole operation.

  Main fetch failure reasons (success=False):
    "fetch_failed"                — network error, timeout, or non-200
    "unexpected_indicator_count"  — parsed count != 23
    "unexpected_indicator_value"  — a class value other than yes/no found

  Historical fetch failure (success=True, historical_levels=None):
    "historical_fetch_failed"     — non-200, network error, or JSON parse error
"""

import html as _html_stdlib
import json
import re
from html.parser import HTMLParser

import requests

_TPI_BASE_URL = "https://www.transitionpathwayinitiative.org/companies/"
_CHART_DATA_PATH = "assessments_levels_chart_data"
_EXPECTED_INDICATOR_COUNT = 23
_FETCH_TIMEOUT = 15  # seconds; TPI pages can be slow


# ---------------------------------------------------------------------------
# Static framework data (sourced from TPI methodology, confirmed 2026-06-28)
# ---------------------------------------------------------------------------

# TPI_MQ_INDICATORS: the 23 Management Quality indicator question texts, in
# assessment order. Same character as NZIF_CRITERIA in criterion_evidence.py:
# methodology facts, not per-company facts.
TPI_MQ_INDICATORS: dict[int, str] = {
    1: "Does the company acknowledge climate change as a significant issue for the business?",
    2: "Does the company recognise climate change as a relevant risk and/or opportunity for the business?",
    3: "Does the company have a policy (or equivalent) commitment to action on climate change?",
    4: "Has the company set greenhouse gas emission reduction targets?",
    5: "Has the company published information on its Scope 1 and 2 greenhouse gas emissions?",
    6: "Has the company nominated a board member or board committee with explicit responsibility for oversight of the climate change policy?",
    7: "Has the company set quantitative targets for reducing its greenhouse gas emissions?",
    8: "Does the company report on Scope 3 emissions?",
    9: "Has the company had its operational (Scope 1 and/or 2) greenhouse gas emissions data verified?",
    10: "Does the company support domestic and international efforts to mitigate climate change?",
    11: "Does the company have a process to manage climate-related risks?",
    12: "Does the company disclose materially important Scope 3 emissions?",
    13: "Has the company set long-term quantitative targets for reducing its greenhouse gas emissions?",
    14: "Does the company's remuneration for senior executives incorporate climate change performance?",
    15: "Does the company incorporate climate change risks and opportunities in their strategy?",
    16: "Does the company undertake climate scenario planning?",
    17: "Does the company disclose an internal price of carbon?",
    18: "Does the company disclose the actions necessary to meet its emissions-reduction targets?",
    19: "Does the company quantify the key elements of its emissions reduction strategy and the proportional impact of each action in achieving its targets?",
    20: "Does the company's transition plan clarify the role that will be played by offsets and/or negative emissions technologies?",
    21: "Does the company commit to phasing out capital expenditure on carbon intensive assets or products?",
    22: "Does the company align future capital expenditures with its long-term decarbonisation goals and disclose how the alignment is determined?",
    23: "Does the company ensure consistency between its climate change policy and the positions taken by trade associations of which it is a member?",
}

# TPI_MQ_LEVEL_BOUNDARIES: which indicator numbers belong to each TPI level.
# A company achieves a level when it passes all indicators up to and including
# that level. Level 0 = not assessed (no indicators required).
# Source: TPI Management Quality methodology, confirmed against levelN CSS
# class attributes on the real TotalEnergies page, 2026-06-28.
TPI_MQ_LEVEL_BOUNDARIES: dict[int, list[int]] = {
    0: [],
    1: [1, 2, 3, 4, 5],
    2: [6, 7, 8, 9, 10],
    3: [11, 12, 13, 14, 15],
    4: [16, 17, 18, 19],
    5: [20, 21, 22, 23],
}


# ---------------------------------------------------------------------------
# HTML parser
# ---------------------------------------------------------------------------


class _MQParser(HTMLParser):
    """
    Reads a TPI company page's raw HTML in a single pass, collecting:
      - mq-answer div classes (yes/no per indicator, in document order)
      - the "Current level" integer from visible text
      - the RemoteDropdown assessment history (date labels + assessment IDs)
      - the company's numeric ID (extracted from the dropdown's "url" field)

    After feed() + finish(), inspect:
      self.indicators       — list of "yes" or "no" strings, document order
      self.values_ok        — False if any unexpected class value encountered
      self.overall_level    — int | None
      self.dropdown_options — list of (date_str, assessment_id) tuples
      self.company_id       — str | None  (e.g. "1216")
    """

    _MQ_ANSWER_RE = re.compile(r"\bmq-answer\b")
    _RESULT_RE = re.compile(r"\bmq-answer--(yes|no)\b")
    _LEVEL_TEXT_RE = re.compile(r"Current\s+level[:\s]+(\d+)", re.IGNORECASE)
    _COMPANY_ID_RE = re.compile(r"/companies/(\d+)/")

    def __init__(self):
        super().__init__()
        self.indicators: list[str] = []
        self.values_ok: bool = True
        self.overall_level: int | None = None
        self.dropdown_options: list[tuple[str, int]] = []
        self.company_id: str | None = None
        self._text_parts: list[str] = []

    def _parse_dropdown_props(self, raw_props: str) -> None:
        """
        Decode HTML-entity-encoded JSON from a RemoteDropdown data-react-props
        attribute and extract assessment history options and company ID.
        Silently ignores malformed props — the dropdown is supplementary data,
        not required for the main indicator parse.
        """
        try:
            decoded = _html_stdlib.unescape(raw_props)
            props = json.loads(decoded)
            # The page has multiple RemoteDropdown elements (MQ and CP
            # assessments). Only the one with name="mq_assessment_id" has the
            # Management Quality history we need; the CP dropdown has entirely
            # different assessment IDs that would 404 on the MQ chart endpoint.
            if props.get("name") != "mq_assessment_id":
                return
            options = props.get("data", [])
            self.dropdown_options = [
                (opt["label"], int(opt["value"])) for opt in options
            ]
            url = props.get("url", "")
            m = self._COMPANY_ID_RE.search(url)
            if m:
                self.company_id = m.group(1)
        except Exception:
            pass  # silently ignore; dropdown absence does not abort indicator parse

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "div":
            return
        attrs_dict = dict(attrs)

        # RemoteDropdown carries the historical assessment list and company ID.
        if attrs_dict.get("data-react-class") == "RemoteDropdown":
            self._parse_dropdown_props(attrs_dict.get("data-react-props", ""))
            return

        cls = attrs_dict.get("class", "")
        if not self._MQ_ANSWER_RE.search(cls):
            return
        m = self._RESULT_RE.search(cls)
        if m:
            self.indicators.append(m.group(1))  # "yes" or "no"
        else:
            # class has "mq-answer" but neither "--yes" nor "--no"
            self.values_ok = False

    def handle_data(self, data):
        self._text_parts.append(data)

    def finish(self) -> None:
        """Call after feed() to post-process plain-text data."""
        full_text = " ".join(self._text_parts)
        m = self._LEVEL_TEXT_RE.search(full_text)
        if m:
            self.overall_level = int(m.group(1))


# ---------------------------------------------------------------------------
# Chart data parsing
# ---------------------------------------------------------------------------


def _parse_chart_response(
    data: list,
) -> tuple[list[tuple[str, int]] | None, tuple[str, int] | None, int | None]:
    """
    Extract (historical_levels, current_level_entry, max_level) from the chart
    endpoint's JSON body.

    Expected shape (confirmed 2026-06-28):
        [
          {"name": "Level",         "data": [["01/07/2017", 3], ...]},
          {"name": "Current Level", "data": [["01/12/2024", 5]]},
          {"name": "Max Level",     "data": 5},
        ]

    current_level_entry is the single (date_str, level_int) pair from the
    "Current Level" series — the date of the most recent assessment and the
    level it produced. The real response's "Current Level" data is a list with
    exactly one entry.

    Returns (None, None, None) if the expected series are absent or malformed.
    """
    historical: list[tuple[str, int]] | None = None
    current_level_entry: tuple[str, int] | None = None
    max_level: int | None = None
    for series in data:
        name = series.get("name")
        raw = series.get("data")
        if name == "Level" and isinstance(raw, list):
            try:
                historical = [(row[0], int(row[1])) for row in raw if len(row) >= 2]
            except (TypeError, ValueError, IndexError):
                historical = None
        elif name == "Current Level" and isinstance(raw, list) and raw:
            try:
                row = raw[0]
                current_level_entry = (row[0], int(row[1]))
            except (TypeError, ValueError, IndexError):
                current_level_entry = None
        elif name == "Max Level" and isinstance(raw, (int, float)):
            max_level = int(raw)
    return historical, current_level_entry, max_level


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_tpi_management_quality(company_slug: str) -> dict:
    """
    Fetch and parse TPI Management Quality data for `company_slug`.

    Makes two HTTP requests:
      1. The company's TPI profile page — for the indicator grid, overall
         level, and the RemoteDropdown carrying historical assessment IDs
         and the company's numeric ID.
      2. The chart-data endpoint — for the full historical level series.
         Only attempted if the first fetch succeeds and the company ID was
         found in the page. A failure here does not discard indicator results.

    Args:
        company_slug: the URL slug used by TPI, e.g. "totalenergies".

    Returns on success (indicator parse succeeded):
        {
            "success": True,
            "overall_level": int | None,
            "indicators": {1: "yes", ..., 23: "no"},
            "historical_levels": [(date_str, level_int), ...] | None,
            "current_level_date": str | None,
            "max_level": int | None,
            "failure_reason": None,
            "historical_fetch_failure_reason": str | None,
        }

    Returns on main-page failure:
        {
            "success": False,
            "overall_level": None,
            "indicators": None,
            "historical_levels": None,
            "current_level_date": None,
            "max_level": None,
            "failure_reason": str,
            "historical_fetch_failure_reason": None,
        }

    failure_reason values (main fetch):
        "fetch_failed"                — network error, timeout, or non-200
        "unexpected_indicator_count"  — count of mq-answer divs != 23
        "unexpected_indicator_value"  — a class value other than yes/no found

    historical_fetch_failure_reason values (secondary fetch; success=True):
        "historical_fetch_failed"     — non-200, network error, or JSON error
    """

    def _fail(reason: str) -> dict:
        return {
            "success": False,
            "overall_level": None,
            "indicators": None,
            "historical_levels": None,
            "current_level_date": None,
            "max_level": None,
            "failure_reason": reason,
            "historical_fetch_failure_reason": None,
        }

    # --- First fetch: company profile page ---
    url = _TPI_BASE_URL + company_slug
    try:
        response = requests.get(url, timeout=_FETCH_TIMEOUT)
    except requests.exceptions.Timeout:
        return _fail("fetch_failed")
    except requests.exceptions.RequestException:
        return _fail("fetch_failed")

    if response.status_code != 200:
        return _fail("fetch_failed")

    html_content = response.content.decode("utf-8", errors="replace")

    parser = _MQParser()
    parser.feed(html_content)
    parser.finish()

    if not parser.values_ok:
        return _fail("unexpected_indicator_value")

    if len(parser.indicators) != _EXPECTED_INDICATOR_COUNT:
        return _fail("unexpected_indicator_count")

    indicators = {i + 1: v for i, v in enumerate(parser.indicators)}

    # --- Second fetch: historical chart data ---
    # Only attempted when the first fetch found a company ID and assessment list.
    # Failure here is isolated: indicator results are preserved.
    historical_levels: list[tuple[str, int]] | None = None
    current_level_date: str | None = None
    max_level: int | None = None
    historical_fail: str | None = None

    if parser.company_id and parser.dropdown_options:
        # Any valid assessment ID retrieves the full Level series.
        any_id = parser.dropdown_options[0][1]
        chart_url = (
            f"{_TPI_BASE_URL}{parser.company_id}"
            f"/{_CHART_DATA_PATH}?mq_assessment_id={any_id}"
        )
        try:
            chart_resp = requests.get(chart_url, timeout=_FETCH_TIMEOUT)
            if chart_resp.status_code != 200:
                historical_fail = "historical_fetch_failed"
            else:
                chart_data = json.loads(chart_resp.content.decode("utf-8"))
                historical_levels, current_entry, max_level = _parse_chart_response(
                    chart_data
                )
                if current_entry is not None:
                    current_level_date = current_entry[0]
        except Exception:
            historical_fail = "historical_fetch_failed"

    return {
        "success": True,
        "overall_level": parser.overall_level,
        "indicators": indicators,
        "historical_levels": historical_levels,
        "current_level_date": current_level_date,
        "max_level": max_level,
        "failure_reason": None,
        "historical_fetch_failure_reason": historical_fail,
    }
