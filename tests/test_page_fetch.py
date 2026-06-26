"""
Tests for page_fetch.py.

All tests here are deterministic — they mock requests.get so no real HTTP
calls are made, consistent with how extraction.py's tests mock the LLM call.
The PDF tests also patch page_fetch.PdfReader so no real PDF bytes need to be
constructed.

What is tested:
  - Successful HTML and PDF fetches return success=True with extracted text.
  - Timeout, connection error, and non-200 statuses each return success=False
    with the correct, specific failure_reason and no exceptions raised.
  - Content-type classification uses the real Content-Type header, not the URL
    string: a URL with no file extension but Content-Type: application/pdf
    takes the PDF extraction path.
  - NO Content-Length (chunked transfer encoding) with a small body now
    SUCCEEDS: the body is streamed with a running cap and assembled fully
    if it completes under the cap. (Previously returned "size_unknown" and
    refused immediately — that behaviour was wrong for legitimate small pages
    served with chunked encoding, e.g. TSMC's Drupal-generated press releases.)
  - NO Content-Length with an oversized body returns "too_large" and confirms
    streaming stops early — the full body is never fully read/buffered.
  - Content-Length present but over the type-specific cap returns "too_large"
    with no body download (fast-path, unchanged behaviour).
  - HTML (2 MB) and PDF (20 MB) caps are tested separately, plus a case that
    verifies the PDF cap applies to PDFs (a size over HTML cap but under PDF
    cap must succeed).
  - An unsupported content type returns "unsupported_content_type" regardless
    of declared size.
  - download_error (body fails mid-stream) and parse_error (body arrived but
    extraction raised) are distinct failure reasons.
"""

import requests
from unittest.mock import MagicMock, patch

from page_fetch import fetch_page_text, HTML_MAX_BYTES, PDF_MAX_BYTES


def _mock_response(
    status_code=200,
    content_type="text/html; charset=utf-8",
    content_length=None,
    body=b"<html><body>Hello world</body></html>",
):
    """
    Build a minimal mock requests.Response.

    content_length=None means the Content-Length header is absent entirely
    (not zero). iter_content is set up to yield the body as a single chunk,
    supporting tests that exercise the streaming path.
    """
    mock = MagicMock()
    mock.status_code = status_code
    headers = {}
    if content_type is not None:
        headers["Content-Type"] = content_type
    if content_length is not None:
        headers["Content-Length"] = str(content_length)
    mock.headers = headers
    mock.content = body
    mock.iter_content.return_value = iter([body])
    return mock


HTML_BODY = (
    b"<html><body>"
    b"<p>TSMC moved its renewable target to 2040 from 2050.</p>"
    b"</body></html>"
)
PDF_TEXT = "TSMC moved its renewable energy target to 2040 from 2050."


# --- successful fetches ---


def test_successful_html_fetch_returns_text():
    mock_resp = _mock_response(
        content_type="text/html; charset=utf-8",
        content_length=len(HTML_BODY),
        body=HTML_BODY,
    )
    with patch("requests.get", return_value=mock_resp):
        result = fetch_page_text("https://tsmc.com/press")
    assert result["success"] is True
    assert "2040" in result["text"]
    assert result["failure_reason"] is None
    assert result["content_type"] == "text/html; charset=utf-8"


def test_successful_pdf_fetch_returns_text():
    fake_pdf_bytes = b"%PDF-1.4 fake"
    mock_resp = _mock_response(
        content_type="application/pdf",
        content_length=len(fake_pdf_bytes),
        body=fake_pdf_bytes,
    )
    mock_page = MagicMock()
    mock_page.extract_text.return_value = PDF_TEXT
    mock_reader = MagicMock()
    mock_reader.pages = [mock_page]
    with patch("requests.get", return_value=mock_resp):
        with patch("page_fetch.PdfReader", return_value=mock_reader):
            result = fetch_page_text("https://tsmc.com/sustainability-report.pdf")
    assert result["success"] is True
    assert "2040" in result["text"]
    assert result["failure_reason"] is None


def test_html_strips_script_and_style_tags():
    body = (
        b"<html><head>"
        b"<script>alert('injected')</script>"
        b"<style>body{color:red}</style>"
        b"</head><body><p>Visible claim text 2040</p></body></html>"
    )
    mock_resp = _mock_response(
        content_type="text/html",
        content_length=len(body),
        body=body,
    )
    with patch("requests.get", return_value=mock_resp):
        result = fetch_page_text("https://tsmc.com/press")
    assert result["success"] is True
    assert "injected" not in result["text"]
    assert "color:red" not in result["text"]
    assert "2040" in result["text"]


# --- connection-level failures (no exceptions raised) ---


def test_timeout_returns_failure_no_exception():
    with patch("requests.get", side_effect=requests.exceptions.Timeout):
        result = fetch_page_text("https://tsmc.com/press")
    assert result["success"] is False
    assert result["failure_reason"] == "timeout"
    assert result["text"] is None


def test_connection_error_returns_failure_no_exception():
    with patch("requests.get", side_effect=requests.exceptions.ConnectionError):
        result = fetch_page_text("https://unreachable.tsmc.com/press")
    assert result["success"] is False
    assert result["failure_reason"] == "connection_error"
    assert result["text"] is None


# --- HTTP status failures ---


def test_404_returns_not_found():
    with patch("requests.get", return_value=_mock_response(status_code=404)):
        result = fetch_page_text("https://tsmc.com/missing")
    assert result["success"] is False
    assert result["failure_reason"] == "not_found"


def test_403_returns_forbidden():
    with patch("requests.get", return_value=_mock_response(status_code=403)):
        result = fetch_page_text("https://tsmc.com/private")
    assert result["success"] is False
    assert result["failure_reason"] == "forbidden"


def test_401_returns_forbidden():
    with patch("requests.get", return_value=_mock_response(status_code=401)):
        result = fetch_page_text("https://tsmc.com/private")
    assert result["success"] is False
    assert result["failure_reason"] == "forbidden"


def test_500_returns_http_error():
    with patch("requests.get", return_value=_mock_response(status_code=500)):
        result = fetch_page_text("https://tsmc.com/broken")
    assert result["success"] is False
    assert result["failure_reason"] == "http_error"


# --- content-type from response header, not URL string ---


def test_content_type_determined_by_header_not_url():
    """
    A URL with no file extension but Content-Type: application/pdf must take
    the PDF path. This verifies classification reads the header, not the URL.
    """
    fake_pdf_bytes = b"%PDF-1.4 fake"
    mock_resp = _mock_response(
        content_type="application/pdf",
        content_length=len(fake_pdf_bytes),
        body=fake_pdf_bytes,
    )
    mock_page = MagicMock()
    mock_page.extract_text.return_value = "annual report text 2023"
    mock_reader = MagicMock()
    mock_reader.pages = [mock_page]
    # URL has no extension — extension-based logic would not know this is a PDF
    with patch("requests.get", return_value=mock_resp):
        with patch("page_fetch.PdfReader", return_value=mock_reader):
            result = fetch_page_text("https://tsmc.com/download/document")
    assert result["success"] is True
    assert "annual report text" in result["text"]


# --- no Content-Length (chunked transfer encoding) ---


def test_no_content_length_small_body_succeeds():
    """
    A response without Content-Length (e.g. chunked transfer encoding on a
    dynamically-generated page) must now SUCCEED if the body completes under
    the cap. Previously this returned "size_unknown" and refused immediately —
    the wrong outcome for a legitimate small page. Found via a live test
    against TSMC's Drupal-served press release at pr.tsmc.com/english/news/3067.
    """
    mock_resp = _mock_response(
        content_type="text/html; charset=utf-8",
        content_length=None,  # no Content-Length header
        body=HTML_BODY,
    )
    # iter_content already set up by _mock_response to yield HTML_BODY
    with patch("requests.get", return_value=mock_resp):
        result = fetch_page_text("https://tsmc.com/press")
    assert result["success"] is True
    assert "2040" in result["text"]
    assert result["failure_reason"] is None


def test_no_content_length_oversized_body_returns_too_large():
    """
    A response without Content-Length whose streaming body exceeds the cap
    must fail as "too_large" — and streaming must stop as soon as the cap is
    crossed, not after the full body is read. Verified by counting how many
    chunks the generator actually yielded before the function returned.
    """
    chunk_size = 8192
    # Enough chunks to definitively cross HTML_MAX_BYTES
    n_chunks = (HTML_MAX_BYTES // chunk_size) + 2
    chunks_yielded = [0]

    def _oversized_gen():
        chunk = b"x" * chunk_size
        for _ in range(n_chunks):
            chunks_yielded[0] += 1
            yield chunk

    mock_resp = _mock_response(
        content_type="text/html; charset=utf-8",
        content_length=None,
        body=b"",  # .content is not used for the streaming path
    )
    mock_resp.iter_content.return_value = _oversized_gen()

    with patch("requests.get", return_value=mock_resp):
        result = fetch_page_text("https://tsmc.com/huge-page")

    assert result["success"] is False
    assert result["failure_reason"] == "too_large"
    # Streaming stopped as soon as the cap was crossed — not all chunks consumed.
    assert chunks_yielded[0] < n_chunks


# --- Content-Length present and over cap (fast-path, unchanged) ---


def test_html_over_cap_returns_too_large():
    over_cap = HTML_MAX_BYTES + 1
    mock_resp = _mock_response(
        content_type="text/html; charset=utf-8",
        content_length=over_cap,
    )
    with patch("requests.get", return_value=mock_resp):
        result = fetch_page_text("https://tsmc.com/huge-page")
    assert result["success"] is False
    assert result["failure_reason"] == "too_large"


def test_pdf_over_cap_returns_too_large():
    over_cap = PDF_MAX_BYTES + 1
    mock_resp = _mock_response(
        content_type="application/pdf",
        content_length=over_cap,
    )
    with patch("requests.get", return_value=mock_resp):
        result = fetch_page_text("https://tsmc.com/huge.pdf")
    assert result["success"] is False
    assert result["failure_reason"] == "too_large"


def test_pdf_over_html_cap_but_under_pdf_cap_passes():
    """
    A PDF whose declared size exceeds the HTML cap (2 MB) but not the PDF cap
    (20 MB) must succeed. Verifies the caps are applied per content type, not
    as a single shared limit.
    """
    size_between_caps = HTML_MAX_BYTES + 1  # > HTML cap, < PDF cap
    fake_pdf_bytes = b"%PDF-1.4 fake"
    mock_resp = _mock_response(
        content_type="application/pdf",
        content_length=size_between_caps,
        body=fake_pdf_bytes,
    )
    mock_page = MagicMock()
    mock_page.extract_text.return_value = "annual report content"
    mock_reader = MagicMock()
    mock_reader.pages = [mock_page]
    with patch("requests.get", return_value=mock_resp):
        with patch("page_fetch.PdfReader", return_value=mock_reader):
            result = fetch_page_text("https://tsmc.com/large-report.pdf")
    assert result["success"] is True
    assert result["failure_reason"] is None


# --- download_error vs parse_error ---


def test_download_error_when_response_content_raises():
    """
    A connection drop after headers arrive but before the full body is
    received raises inside response.content. This must return
    failure_reason="download_error", distinct from parse_error (body
    arrived but extraction failed). Uses a _DroppingResponse subclass
    to make .content raise a ConnectionError.
    """

    class _DroppingResponse(MagicMock):
        @property
        def content(self):
            raise requests.exceptions.ConnectionError("connection dropped mid-stream")

    mock_resp = _DroppingResponse()
    mock_resp.status_code = 200
    mock_resp.headers = {
        "Content-Type": "text/html; charset=utf-8",
        "Content-Length": "1024",
    }
    with patch("requests.get", return_value=mock_resp):
        result = fetch_page_text("https://tsmc.com/press")
    assert result["success"] is False
    assert result["failure_reason"] == "download_error"


def test_parse_error_when_extraction_raises_after_successful_download():
    """
    Body downloads successfully (response.content returns bytes) but
    PdfReader raises during extraction. Must return failure_reason=
    "parse_error", not "download_error" — the body arrived, only the
    parsing step failed.
    """
    fake_pdf_bytes = b"%PDF-1.4 corrupted"
    mock_resp = _mock_response(
        content_type="application/pdf",
        content_length=len(fake_pdf_bytes),
        body=fake_pdf_bytes,
    )
    with patch("requests.get", return_value=mock_resp):
        with patch(
            "page_fetch.PdfReader", side_effect=Exception("malformed PDF stream")
        ):
            result = fetch_page_text("https://tsmc.com/corrupted.pdf")
    assert result["success"] is False
    assert result["failure_reason"] == "parse_error"


# --- unsupported content type ---


def test_unsupported_content_type_returns_failure():
    mock_resp = _mock_response(
        content_type="image/png",
        content_length=1024,
    )
    with patch("requests.get", return_value=mock_resp):
        result = fetch_page_text("https://tsmc.com/logo.png")
    assert result["success"] is False
    assert result["failure_reason"] == "unsupported_content_type"
