"""
Tests for domain_check.py.

These tests exist specifically to lock in the spoofing-resistance behavior
discussed during design: endswith-based matching, not startswith or
substring containment. If someone "simplifies" the implementation later,
these tests should fail loudly.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

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
