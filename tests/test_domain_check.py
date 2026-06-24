"""
Tests for domain_check.py.

These tests exist specifically to lock in the spoofing-resistance behavior
discussed during design: endswith-based matching, not startswith or
substring containment. If someone "simplifies" the implementation later,
these tests should fail loudly.
"""

from domain_check import check_domain


def test_exact_domain_match():
    result = check_domain("https://tsmc.com/news", ["tsmc.com"])
    assert result["passed"] is True
    assert result["matched_entry"] == "tsmc.com"


def test_legitimate_subdomain_passes():
    result = check_domain("https://pr.tsmc.com/english/news/3067", ["tsmc.com"])
    assert result["passed"] is True
    assert result["matched_entry"] == "tsmc.com"


def test_unrelated_domain_fails():
    result = check_domain("https://example.com/news", ["tsmc.com"])
    assert result["passed"] is False
    assert result["matched_entry"] is None


def test_prefix_spoof_does_not_match():
    """
    "nottsmc.com" must NOT match against allowlist entry "tsmc.com".
    There is no "." before "tsmc" in this string, so endswith(".tsmc.com")
    correctly fails.
    """
    result = check_domain("https://nottsmc.com/fake-news", ["tsmc.com"])
    assert result["passed"] is False


def test_suffix_spoof_does_not_match():
    """
    "tsmc.com.evil.com" must NOT match against allowlist entry "tsmc.com".
    This is the realistic spoofing case: the real domain appears as a
    PREFIX of the spoofed domain. A naive startswith() or substring check
    would wrongly pass this. endswith() correctly rejects it because the
    domain must END in the allowlisted entry, not merely contain it.
    """
    result = check_domain("https://tsmc.com.evil.com/fake-news", ["tsmc.com"])
    assert result["passed"] is False


def test_www_prefix_is_stripped_before_comparison():
    result = check_domain("https://www.tsmc.com/news", ["tsmc.com"])
    assert result["passed"] is True


def test_case_insensitivity():
    result = check_domain("https://PR.TSMC.COM/news", ["tsmc.com"])
    assert result["passed"] is True


def test_port_injection_exploit_is_blocked():
    """
    Found via adversarial review (Gemini), confirmed as a live bypass before
    the fix: a malformed URL like "https://evil.com:.tsmc.com/fake-news"
    produces a netloc of "evil.com:.tsmc.com", which ends with ".tsmc.com"
    and would WRONGLY pass if netloc were used directly. parsed.hostname
    correctly identifies the real host as "evil.com", which must fail.
    """
    result = check_domain("https://evil.com:.tsmc.com/fake-news", ["tsmc.com"])
    assert result["passed"] is False
    assert result["domain"] == "evil.com"


def test_legitimate_url_with_port_passes():
    """
    Found via adversarial review (both models): a legitimate URL with an
    explicit port, e.g. "https://tsmc.com:443/news", was a false negative
    under the old netloc-based extraction. parsed.hostname strips the port.
    """
    result = check_domain("https://tsmc.com:443/news", ["tsmc.com"])
    assert result["passed"] is True
    assert result["domain"] == "tsmc.com"


def test_trailing_dot_fqdn_passes():
    """
    Found via adversarial review (Gemini): "tsmc.com." (trailing dot) is a
    technically valid, fully-qualified DNS name referring to the same host
    as "tsmc.com". Must not be rejected as a different domain.
    """
    result = check_domain("https://tsmc.com./news", ["tsmc.com"])
    assert result["passed"] is True


def test_credentials_in_url_do_not_cause_false_pass_or_unexpected_crash():
    """
    Found via adversarial review (both models): URLs can embed credentials
    (user:pass@host). parsed.hostname ignores this section entirely, so the
    real host is still correctly identified.
    """
    result = check_domain("https://user:pass@tsmc.com/news", ["tsmc.com"])
    assert result["passed"] is True
    assert result["domain"] == "tsmc.com"


def test_schemeless_url_fails_safe():
    """
    A URL with no scheme (e.g. raw text "tsmc.com/news" rather than a full
    "https://tsmc.com/news") has no parseable netloc/hostname. This must
    fail closed (rejected), not pass open, since there is no host to verify
    against the allowlist.
    """
    result = check_domain("tsmc.com/news", ["tsmc.com"])
    assert result["passed"] is False
