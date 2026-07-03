"""
page_fetch.py

Retrieves the text content of a URL for use as the `document` argument to
match_quote() and the existing pipeline. This is the fetch step that slots
in between domain_check passing (confirming a URL is legitimate) and
quote_match running (checking whether a quote appears in the fetched text).

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

SIZE-CHECK DESIGN — streaming cap for responses without Content-Length:

The original design checked the Content-Length header before downloading and
refused immediately if it was missing ("size_unknown") or too large
("too_large"). A live test against TSMC's actual press release URL
(https://pr.tsmc.com/english/news/3067) surfaced a real gap: the server uses
chunked transfer encoding (common for dynamically-generated pages, e.g.
Drupal), which legitimately cannot send Content-Length in advance, by design,
not by misconfiguration. The 184 KB page was correctly refused as "size_unknown"
— a real, small, legitimate document wrongly rejected.

The fix streams the response body in chunks (response.iter_content), tracking
a running cumulative byte count. If the count crosses the type-specific cap
before the stream completes, the download is aborted and "too_large" is
returned — the same outcome as the pre-download Content-Length check, just
detected during streaming for the no-Content-Length case. If the stream
completes under the cap, the full, complete, parseable document is assembled
and returned — identical to the Content-Length-present-and-valid success path
in every way.

This is NOT the mid-download truncation approach considered and rejected in
the original design. That approach cut off at an arbitrary byte count and
attempted to parse whatever partial bytes resulted — a truncated PDF is
invalid, truncated HTML may be mid-tag, and there is no way to know in
advance which you'll get. This fix never parses a partial document: either
the full stream completes (success) or the cap is crossed and the entire
attempt is refused (failure). The original protection against genuinely
oversized responses is fully preserved.

For responses WITH a Content-Length header: the fast-path pre-download check
is kept unchanged. If Content-Length is present and already exceeds the cap,
there is no reason to begin streaming at all.

"size_unknown" AS A FAILURE REASON IS NOW UNREACHABLE:

After this fix, no code path in this module returns "size_unknown". Previously
it fired for: (a) absent Content-Length, and (b) an unparseable Content-Length
value (e.g. "Content-Length: abc"). Case (a) is now handled by streaming.
Case (b) is treated the same way (stream with cap), rather than refusing
outright for what is a malformed-but-harmless header from a mostly-functional
server. "size_unknown" is removed from the failure_reason vocabulary below
rather than left as dead code.

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

# Size caps applied during streaming (no Content-Length) or before download
# (Content-Length present). Starting assumptions based on TSMC press releases
# (~50-200 KB) and annual/sustainability reports (several MB as PDFs).
# Revisit if real documents routinely exceed these. See module docstring,
# "SIZE-CHECK DESIGN", for the reasoning behind streaming rather than
# truncating mid-download.
HTML_MAX_BYTES = 2 * 1024 * 1024  # 2 MB
PDF_MAX_BYTES = 20 * 1024 * 1024  # 20 MB

# Chunk size for streaming reads on responses without Content-Length.
_STREAM_CHUNK_SIZE = 8192


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
    body is downloaded. For responses with a Content-Length header, checks
    it against the type-specific cap before downloading. For responses
    without Content-Length (chunked transfer encoding, etc.), streams the
    body in chunks and enforces the cap continuously during streaming.

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
        "too_large"                — declared Content-Length exceeds the cap,
                                     OR streaming cumulative bytes crossed the
                                     cap before the response completed
        "download_error"           — body failed mid-download (connection drop
                                     after headers arrived but before full body)
        "parse_error"              — body downloaded but extraction failed
                                     (malformed PDF, decode failure, etc.)

    Note: "size_unknown" was removed as a failure reason. Responses without
    a Content-Length header are now handled via streaming with a cumulative
    cap (see module docstring, "SIZE-CHECK DESIGN").
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

    cap = HTML_MAX_BYTES if ct_class == "html" else PDF_MAX_BYTES

    # Parse Content-Length if present. An absent or unparseable value means
    # we fall through to streaming. An invalid value (e.g. "abc") is treated
    # the same as absent — stream with a running cap rather than refusing
    # outright, since the server may still deliver a legitimately sized body.
    cl_header = response.headers.get("Content-Length")
    content_length: int | None = None
    if cl_header is not None:
        try:
            content_length = int(cl_header)
        except ValueError:
            pass  # treat invalid header like absent

    if content_length is not None:
        # Fast-path: Content-Length known before download.
        if content_length > cap:
            return _fail("too_large", content_type=raw_ct)
        # Under cap with known size — download body in one shot.
        try:
            body = response.content
        except Exception:
            return _fail("download_error", content_type=raw_ct)
    else:
        # No Content-Length (chunked transfer encoding, dynamic pages, etc.).
        # Stream in chunks and enforce the cap as bytes accumulate. Abort if
        # the running total crosses the cap — never return a partial document.
        chunks: list[bytes] = []
        total = 0
        try:
            for chunk in response.iter_content(chunk_size=_STREAM_CHUNK_SIZE):
                total += len(chunk)
                if total > cap:
                    return _fail("too_large", content_type=raw_ct)
                chunks.append(chunk)
            body = b"".join(chunks)
        except Exception:
            return _fail("download_error", content_type=raw_ct)

    try:
        text = _html_to_text(body) if ct_class == "html" else _pdf_to_text(body)
    except Exception:
        return _fail("parse_error", content_type=raw_ct)

    return {
        "success": True,
        "text": text,
        "content_type": raw_ct,
        "failure_reason": None,
    }
