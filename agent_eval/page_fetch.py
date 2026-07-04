"""URL -> plain text fetch with size caps and named failure reasons.

The fetch step between domain_check passing and quote_match running. Extracts
plain text from HTML pages and clean, digitally-created PDFs only — no table
extraction, no OCR, no scanned-document handling (deferred until Bucket C
grows a consumer for table data; the swap would be isolated to _pdf_to_text).

Size handling: responses with a Content-Length header are checked against a
type-specific cap before download; responses without one (chunked transfer
encoding — a real TSMC press release surfaced this) are streamed with a
running cumulative cap. A partial document is never parsed: either the full
stream completes or the whole attempt is refused as "too_large".

No retry logic lives here — this module reports honestly what happened to
exactly one URL, and extraction.py's loop decides what to do about it.

Dependency choices (requests over httpx, MIT-licensed pypdf over AGPL
PyMuPDF) and the chunked-encoding bug that reshaped the size check are
recorded in adr/0007-page-fetch.md.
"""

import io
from html.parser import HTMLParser
from typing import TypedDict

import requests
from pypdf import PdfReader


class FetchResult(TypedDict):
    """The result contract every fetch consumer depends on.

    success=True guarantees text is a str; on failure text is None and
    failure_reason names the specific failure (see fetch_page_text).

    final_url is the URL the content actually came from after any HTTP
    redirects (requests follows them by default). A URL that passes the
    domain check can redirect off-domain, so verification layers must
    re-validate final_url rather than trust the requested URL — see
    adr/0023-redirect-revalidation.md. None on failure.
    """

    success: bool
    text: str | None
    content_type: str | None
    failure_reason: str | None
    final_url: str | None


# Size caps applied during streaming (no Content-Length) or before download
# (Content-Length present). Starting assumptions based on TSMC press releases
# (~50-200 KB) and annual/sustainability reports (several MB as PDFs).
# Revisit if real documents routinely exceed these.
HTML_MAX_BYTES = 2 * 1024 * 1024  # 2 MB
PDF_MAX_BYTES = 20 * 1024 * 1024  # 20 MB

# Chunk size for streaming reads on responses without Content-Length.
_STREAM_CHUNK_SIZE = 8192


class _TextExtractor(HTMLParser):
    """
    Minimal HTML-to-text extractor. Skips script/style blocks; strips all
    other tags and collects visible text. Whitespace-normalized on output.
    """

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth: int = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in ("script", "style"):
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in ("script", "style") and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
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


def fetch_page_text(url: str, timeout: int = 10) -> FetchResult:
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
            "final_url":      str | None,   # post-redirect URL; None on failure
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
    """

    def _fail(reason: str, content_type: str | None = None) -> FetchResult:
        return {
            "success": False,
            "text": None,
            "content_type": content_type,
            "failure_reason": reason,
            "final_url": None,
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
        # The post-redirect URL the body actually came from. Consumers that
        # gated on the requested URL's domain must re-validate this one.
        "final_url": str(response.url),
    }
