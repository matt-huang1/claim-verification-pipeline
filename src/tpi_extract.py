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

where N is an integer (0–5) representing the NZIF level the indicator belongs
to. The divs appear in document order, matching indicator numbers 1 through 23.
Cross-checked against an independent RBC analyst document that independently
stated TotalEnergies fails indicators 21 and 22 only — the HTML parse matched
exactly.

THIS STRUCTURE IS CONFIRMED FOR EXACTLY ONE COMPANY'S PAGE (TotalEnergies).
It is not assumed universal. A different company's page may have a different
number of applicable indicators, or TPI may change their page template. The
function treats any unexpected indicator count or class value as an honest
failure rather than silently guessing — the same discipline applied in
page_fetch.py for unexpected content types and in extraction.py for malformed
LLM responses.

HONEST FAILURE DESIGN:

Three distinct failure reasons are used (never raised, always returned):

  "fetch_failed"                 — network error, timeout, or non-200
  "unexpected_indicator_count"   — parsed count != 23; page structure differs
                                   from the confirmed TotalEnergies template
  "unexpected_indicator_value"   — a class value other than "yes" or "no"
                                   was found; parse did not produce a clean
                                   binary result for every indicator
"""

import re
from html.parser import HTMLParser

import requests

_TPI_BASE_URL = "https://www.transitionpathwayinitiative.org/companies/"
_EXPECTED_INDICATOR_COUNT = 23
_FETCH_TIMEOUT = 15  # seconds; TPI pages can be slow


class _MQParser(HTMLParser):
    """
    Collects mq-answer div classes and the "Current level" integer from the
    raw HTML of a TPI company page.

    After feed(), inspect:
      self.indicators  — list of "yes" or "no" strings, in document order
      self.values_ok   — False if any unexpected class value was encountered
      self.overall_level — int | None
    """

    _MQ_ANSWER_RE = re.compile(r"\bmq-answer\b")
    _RESULT_RE = re.compile(r"\bmq-answer--(yes|no)\b")
    _LEVEL_TEXT_RE = re.compile(r"Current\s+level[:\s]+(\d+)", re.IGNORECASE)

    def __init__(self):
        super().__init__()
        self.indicators: list[str] = []
        self.values_ok: bool = True
        self.overall_level: int | None = None
        self._in_body: bool = True  # html.parser feeds the whole document
        self._text_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "div":
            return
        cls = dict(attrs).get("class", "")
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


def extract_tpi_management_quality(company_slug: str) -> dict:
    """
    Fetch and parse the TPI Management Quality indicator grid for `company_slug`.

    Args:
        company_slug: the URL slug used by TPI, e.g. "totalenergies".

    Returns on success:
        {
            "success": True,
            "overall_level": int | None,  # None if not found in page text
            "indicators": {1: "yes", ..., 23: "no"},
            "failure_reason": None,
        }

    Returns on failure:
        {
            "success": False,
            "overall_level": None,
            "indicators": None,
            "failure_reason": str,
        }

    failure_reason values:
        "fetch_failed"                — network error, timeout, or non-200
        "unexpected_indicator_count"  — count of mq-answer divs != 23
        "unexpected_indicator_value"  — a class value other than yes/no found
    """

    def _fail(reason: str) -> dict:
        return {
            "success": False,
            "overall_level": None,
            "indicators": None,
            "failure_reason": reason,
        }

    url = _TPI_BASE_URL + company_slug
    try:
        response = requests.get(url, timeout=_FETCH_TIMEOUT)
    except requests.exceptions.Timeout:
        return _fail("fetch_failed")
    except requests.exceptions.RequestException:
        return _fail("fetch_failed")

    if response.status_code != 200:
        return _fail("fetch_failed")

    html = response.content.decode("utf-8", errors="replace")

    parser = _MQParser()
    parser.feed(html)
    parser.finish()

    if not parser.values_ok:
        return _fail("unexpected_indicator_value")

    if len(parser.indicators) != _EXPECTED_INDICATOR_COUNT:
        return _fail("unexpected_indicator_count")

    indicators = {i + 1: v for i, v in enumerate(parser.indicators)}

    return {
        "success": True,
        "overall_level": parser.overall_level,
        "indicators": indicators,
        "failure_reason": None,
    }
