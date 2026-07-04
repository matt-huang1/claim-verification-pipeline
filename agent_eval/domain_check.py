"""URL-domain legitimacy check against a caller-supplied allowlist.

Generic and fully deterministic: no companies, no claims, no model call. The
matching rules exist to defeat two confirmed spoofing patterns (prefix domains
and port injection) — see check_domain's docstring for the constraints and
adr/0002-domain-check.md for how each bypass was found.
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

    Two constraints are load-bearing for security (both correspond to
    confirmed, working bypasses — see adr/0002-domain-check.md):

    - Suffix matching must stay endswith("." + entry), never startswith()
      or substring containment: "tsmc.com.evil.com" contains the real
      domain as a prefix (a real spoofing pattern) and must fail, which
      only an anchored suffix check guarantees.
    - The host must come from parsed.hostname, never parsed.netloc:
      netloc includes ports and credentials, so
      "https://evil.com:.tsmc.com/" yields a netloc ending in ".tsmc.com"
      (would wrongly pass) while hostname is "evil.com" (correctly fails).

    Both attacks are permanently regression-tested in the adversarial
    self-evaluation suite (agent_eval/adversarial_eval.py).
    """
    parsed = urlparse(url)
    domain = (parsed.hostname or "").lower()

    # Strip a trailing "." for fully-qualified DNS names (e.g. "tsmc.com.")
    domain = domain.rstrip(".")

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
