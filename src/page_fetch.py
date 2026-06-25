"""
page_fetch.py

Retrieves the text content of a URL for use as the `document` argument to
match_quote() and the existing pipeline. This is the fetch step that slots
in between domain_check passing (confirming a URL is legitimate) and
quote_match running (checking whether a quote appears in the fetched text).

Before this module existed, `document` had to be provided as a hardcoded
string in every test and live extraction run. This module is what makes
that automatic.

SCOPE LIMIT — plain text extraction only:

This module extracts text from HTML pages and from clean, digitally-created
PDFs (PDFs with real selectable text, not scanned images). It deliberately
does NOT handle:

  - Table extraction (reading tabular data as structured rows/columns)
  - OCR (reading text from scanned or image-based PDFs)
  - Scanned document handling of any kind

These limitations are stated here, not buried, for the same reason
quote_match.py documents its numeric-gate scope limit: so that a future
maintainer extending this module to handle Bucket C work (which will need
table data, e.g. for market-share breakdowns) knows exactly where the
current boundary is and why it is there. Table extraction for Bucket C is
a real, distinct future need — it is deferred because quote_match's fuzzy
text matching cannot use table structure anyway, so adding it now would be
pure complexity with no current benefit. pdfplumber is the natural library
to reach for when that work happens; the swap would be isolated entirely to
_pdf_to_text() in this module.

DEPENDENCY CHOICES:

  requests over httpx: this pipeline is synchronous and single-URL-at-a-time
  by design. There is no current need for async or parallel fetching. httpx
  is a clean future swap if that changes — the HTTP call is isolated to this
  module, not woven through the architecture — but it is not justified now.

  pypdf over PyMuPDF: PyMuPDF (fitz) extracts text more reliably from complex
  PDFs but is licensed AGPL v3, which has copyleft implications this project
  wants to avoid. pypdf is MIT-licensed and sufficient for clean, digitally-
  created documents.

SIZE-CHECK DESIGN — why Content-Length before download, not mid-download cap:

The function checks the Content-Length header BEFORE downloading the body,
and refuses to proceed if Content-Length is missing or too large. This is a
deliberate choice over the alternative of downloading and capping mid-stream:

  A mid-download cutoff can produce either a usable truncated document or a
  broken, unparseable one (a truncated PDF is invalid; truncated HTML may be
  mid-tag). There is no reliable way to know which in advance. Replacing a
  clean failure with an unpredictable one does not actually solve the problem.

  Missing Content-Length is treated as an outright failure for the same
  reason: without a declared size there is no way to know whether downloading
  is safe before starting, so the function refuses rather than guessing.

  The caps themselves (HTML_MAX_BYTES = 2 MB, PDF_MAX_BYTES = 20 MB) are
  starting assumptions based on real documents encountered in this project
  (TSMC press releases and sustainability reports), not derived from a
  formula. They should be revisited if real documents routinely exceed them.
  This mirrors the same "documented starting assumption" pattern used for
  AMBIGUITY_GAP_THRESHOLD in quote_match.py and NO_PROGRESS_SCORE_DELTA
  in extraction.py.

NO RETRY LOGIC:

This function reports honestly what happened for a single URL. It does not
retry, fall back to alternative URLs, or compensate for transient failures.
That responsibility belongs entirely to extraction.py's existing retry loop,
which already knows how to react to a specific failure_reason with targeted
feedback on the next attempt. Keeping this module narrowly honest — rather
than making it a second retry mechanism — is the same principle behind
pipeline.py's split into separate check and assembly functions: units that
do one thing stay independently testable and composable.
"""

import io
from html.parser import HTMLParser

import requests
from pypdf import PdfReader

# Size caps applied BEFORE downloading the body. Starting assumptions based
# on TSMC press releases (~50-200 KB) and annual/sustainability reports
# (several MB as PDFs). Revisit if real documents routinely exceed these.
# See module docstring, "SIZE-CHECK DESIGN", for the reasoning behind
# checking before download rather than capping mid-stream.
HTML_MAX_BYTES = 2 * 1024 * 1024  # 2 MB
PDF_MAX_BYTES = 20 * 1024 * 1024  # 20 MB


class _TextExtractor(HTMLParser):
    """
    Minimal HTML-to-text extractor. Skips script/style blocks; strips all
    other tags and collects visible text. Whitespace-normalized on output.
    """

    def __init__(self):
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth: int = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in ("script", "style"):
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in ("script", "style") and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            stripped = data.strip()
            if stripped:
                self._parts.append(stripped)

    def get_text(self) -> str:
        return " ".join(self._parts)


def _html_to_text(html_bytes: bytes) -> str:
    extractor = _TextExtractor()
    extractor.feed(html_bytes.decode("utf-8", errors="replace"))
    return extractor.get_text()


def _pdf_to_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _classify_content_type(content_type_header: str | None) -> str:
    """
    Returns "html", "pdf", or "other" from a raw Content-Type header value.
    Parses only the media-type portion (ignores charset, boundary, etc.).
    A missing or unrecognised header returns "other".
    """
    if not content_type_header:
        return "other"
    media_type = content_type_header.split(";")[0].strip().lower()
    if media_type in ("text/html", "application/xhtml+xml"):
        return "html"
    if media_type == "application/pdf":
        return "pdf"
    return "other"


def fetch_page_text(url: str, timeout: int = 10) -> dict:
    """
    Retrieve the text content of `url`.

    Makes the request with stream=True so headers are available before the
    body is downloaded, checks Content-Length against type-specific caps,
    then downloads and extracts text only if all checks pass.

    Args:
        url:     The URL to fetch. Must already have been confirmed
                 legitimate by check_domain() — this function does not
                 re-validate domains.
        timeout: Request timeout in seconds (connection + read). Default 10.

    Returns a dict:
        {
            "success":        bool,
            "text":           str | None,   # None on failure
            "content_type":   str | None,   # raw Content-Type header value
            "failure_reason": str | None,   # None on success
        }

    failure_reason values:
        "timeout"                  — request timed out
        "connection_error"         — could not reach the server
        "forbidden"                — HTTP 401 or 403
        "not_found"                — HTTP 404
        "http_error"               — any other non-200 status
        "unsupported_content_type" — content type is not html or pdf
        "size_unknown"             — Content-Length header absent
        "too_large"                — Content-Length exceeds the type cap
        "parse_error"              — body downloaded but could not be parsed
    """

    def _fail(reason: str, content_type: str | None = None) -> dict:
        return {
            "success": False,
            "text": None,
            "content_type": content_type,
            "failure_reason": reason,
        }

    try:
        response = requests.get(url, stream=True, timeout=timeout)
    except requests.exceptions.Timeout:
        return _fail("timeout")
    except requests.exceptions.ConnectionError:
        return _fail("connection_error")
    except requests.exceptions.RequestException:
        return _fail("connection_error")

    if response.status_code in (401, 403):
        return _fail("forbidden")
    if response.status_code == 404:
        return _fail("not_found")
    if response.status_code != 200:
        return _fail("http_error")

    raw_ct = response.headers.get("Content-Type")
    ct_class = _classify_content_type(raw_ct)

    if ct_class == "other":
        return _fail("unsupported_content_type", content_type=raw_ct)

    cl_header = response.headers.get("Content-Length")
    if cl_header is None:
        return _fail("size_unknown", content_type=raw_ct)

    try:
        content_length = int(cl_header)
    except ValueError:
        return _fail("size_unknown", content_type=raw_ct)

    cap = HTML_MAX_BYTES if ct_class == "html" else PDF_MAX_BYTES
    if content_length > cap:
        return _fail("too_large", content_type=raw_ct)

    try:
        body = response.content
        text = _html_to_text(body) if ct_class == "html" else _pdf_to_text(body)
    except Exception:
        return _fail("parse_error", content_type=raw_ct)

    return {
        "success": True,
        "text": text,
        "content_type": raw_ct,
        "failure_reason": None,
    }
