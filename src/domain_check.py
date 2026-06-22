"""
domain_check.py

Checks whether a given URL's domain matches an allowlist of known-legitimate
domains for a given entity. This module knows nothing about companies, claims,
or buckets — it only knows URLs and allowlists. Generic by design, so the same
function works regardless of which company or claim is being checked.

This is a deterministic check: given the same URL and the same allowlist, the
result is always identical. No model, no judgment call, no probability.
"""

from urllib.parse import urlparse


def check_domain(url: str, allowlist: list[str]) -> dict:
    """
    Check whether a URL's domain matches an entry in the allowlist.

    Args:
        url: The URL to check, e.g. "https://pr.tsmc.com/english/news/3067"
        allowlist: A list of legitimate domains, e.g. ["tsmc.com", "pr.tsmc.com"]

    Returns:
        A dict with:
            - "domain": the extracted domain from the URL
            - "passed": True if the domain matches an allowlist entry, else False
            - "matched_entry": the allowlist entry that matched, or None

    Matching logic: a URL's domain passes if it is exactly equal to an
    allowlist entry, OR if it is a subdomain of an allowlist entry
    (e.g. "pr.tsmc.com" passes against allowlist entry "tsmc.com").
    This is still fully deterministic — there is no fuzziness here, only
    exact string comparison after parsing.

    IMPORTANT — do not "simplify" this to startswith() or a substring check.
    This function deliberately uses endswith(domain, "." + entry) rather than
    startswith() or `entry in domain`. This matters for security, not just
    style:

        "pr.tsmc.com"        endswith ".tsmc.com" -> True  (legitimate subdomain)
        "tsmc.com.evil.com"  endswith ".tsmc.com" -> False (spoofed domain, correctly rejected)
        "tsmc.com.evil.com"  startswith "tsmc.com" -> True (would WRONGLY pass)

    A domain like "tsmc.com.evil.com" contains the real domain as a prefix,
    a real-world spoofing pattern. Checking startswith() or plain substring
    containment would let this kind of spoofed domain pass. Checking
    endswith() on the entry anchored with a leading "." is what prevents it,
    because the real domain must be the suffix, not just present somewhere
    in the string.
    """
    parsed = urlparse(url)
    domain = parsed.netloc.lower()

    # Strip a leading "www." for consistent comparison
    if domain.startswith("www."):
        domain = domain[4:]

    for entry in allowlist:
        entry_lower = entry.lower()
        if domain == entry_lower or domain.endswith("." + entry_lower):
            return {
                "domain": domain,
                "passed": True,
                "matched_entry": entry_lower,
            }

    return {
        "domain": domain,
        "passed": False,
        "matched_entry": None,
    }
