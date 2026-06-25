"""
Tests for url_compare.py.

same_url() tolerates trivial formatting differences (scheme, www., trailing
slash) and treats everything else — particularly the path — as exact.
"""

from url_compare import same_url

# --- matches (formatting differences only) ---


def test_identical_urls_match():
    assert same_url("https://tsmc.com/news/3067", "https://tsmc.com/news/3067") is True


def test_http_vs_https_match():
    assert same_url("http://tsmc.com/news/3067", "https://tsmc.com/news/3067") is True


def test_www_vs_no_www_match():
    assert (
        same_url("https://www.tsmc.com/news/3067", "https://tsmc.com/news/3067") is True
    )


def test_trailing_slash_vs_no_trailing_slash_match():
    assert same_url("https://tsmc.com/news/3067/", "https://tsmc.com/news/3067") is True


def test_http_www_and_trailing_slash_all_at_once_match():
    assert (
        same_url("http://www.tsmc.com/news/3067/", "https://tsmc.com/news/3067") is True
    )


# --- non-matches (semantically different) ---


def test_different_path_segment_does_not_match():
    """A one-character path difference is a different document."""
    assert same_url("https://tsmc.com/news/3067", "https://tsmc.com/news/3068") is False


def test_different_domains_do_not_match():
    assert (
        same_url("https://tsmc.com/news/3067", "https://samsung.com/news/3067") is False
    )


def test_subdomain_difference_does_not_match():
    """pr.tsmc.com and ir.tsmc.com are different servers."""
    assert (
        same_url("https://pr.tsmc.com/news/3067", "https://ir.tsmc.com/news/3067")
        is False
    )


def test_extra_path_component_does_not_match():
    assert same_url("https://tsmc.com/news", "https://tsmc.com/news/3067") is False
