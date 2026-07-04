"""Same-page URL comparison: cosmetic differences tolerated, path exact.

Compares two URLs, tolerating only differences that provably cannot change
which page you land on (scheme, leading www., trailing slash) and treating
everything else — particularly the path — as exact. A single character
difference in a path can mean a different document, so fuzzy URL comparison
would silently accept exactly the hallucinated-URL failure mode web search
was added to prevent (same principle as quote_match's numeric token gate:
identifiers don't tolerate approximation).

Assumes both URLs carry an explicit scheme; a bare "tsmc.com/news" parses as
a path with no hostname and would compare incorrectly. In practice Tavily
and the LLM always supply a scheme. Design context: adr/0008-url-compare.md.
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
