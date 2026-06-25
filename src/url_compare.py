"""
url_compare.py

Utility for comparing two URLs to determine whether they refer to the same
page, tolerating trivial formatting differences that carry no semantic meaning
(http vs https, leading www., trailing slash on the root path), while treating
everything else — particularly the path — as exact.

WHY PATH COMPARISON IS EXACT:

A single character difference in a URL path can mean a completely different
document. A fuzzy or approximate URL comparison would silently accept a model
that proposed a real-looking URL with a wrong path — exactly the hallucinated-
URL failure mode that web search was added to prevent. The normalization here
is deliberately narrow: only strip things that provably cannot change which page
you land on (scheme, www., trailing slash), and compare everything else exactly.

This is the same principle as quote_match.py's numeric token gate: fuzzy
similarity works for text where slight rephrasing doesn't change meaning, but
not for identifiers (URLs, numbers) where a single character difference is the
entire point.

WHY A SEPARATE MODULE:

This is a standalone utility rather than an inline helper in extraction.py for
the same reason page_fetch.py is separated from extraction.py: URL comparison
is a reusable, independently testable operation. Any future Bucket B/C/D
verification work that needs to check "is this the same source as that one"
should be able to call same_url() directly rather than duplicating normalization
logic.

ASSUMPTIONS:

Both URLs are expected to have an explicit scheme (http:// or https://). A bare
URL without a scheme (e.g. "tsmc.com/news") will not parse correctly — the
entire string will be treated as a path with no hostname, and the comparison
will produce incorrect results. In practice, URLs from the Brave Search API
and from the LLM always include a scheme.
"""

from urllib.parse import urlparse


def _normalize(url: str) -> str:
    """
    Reduce a URL to a scheme-free, www-free, trailing-slash-free
    "hostname + path" string for exact comparison.

    Query strings and fragments are excluded: they are uncommon in the
    press-release/report URLs this project handles, and including them
    would cause a search-result URL with a trailing analytics param to
    fail to match the same URL without it. If a future use case requires
    distinguishing URLs that differ only by query string, this function
    can be extended — the change is isolated here.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path.rstrip("/")
    return host + path


def same_url(url_a: str, url_b: str) -> bool:
    """
    Return True if url_a and url_b refer to the same page after normalization.

    Tolerates:
      - http vs https
      - leading www. vs no www.
      - a trailing slash on the path

    Does NOT tolerate any difference in the path itself (including a one-
    character difference such as .../3067 vs .../3068), or a difference in
    hostname beyond www. stripping.
    """
    return _normalize(url_a) == _normalize(url_b)
