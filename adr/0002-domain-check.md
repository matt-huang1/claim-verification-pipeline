# ADR-0002: domain_check.py — verifying a URL is who it claims to be

## Status

Accepted

## Context

`domain_check.py` checks a claimed source URL's domain against an allowlist of known-legitimate domains for a company. It deliberately knows nothing about companies, claims, or buckets — generic on purpose, so the same function works regardless of what is being checked.

Why domain check exists at all, and why it does not cross-reference third parties: for a company's own self-announcement (a press release), the company's own domain is the authoritative source by definition. A third party reporting on the announcement does not make the announcement more true — it is not a Bucket C "multiple sources, reconcile definitions" situation. Checking three news sites about whether TSMC put out a press release would have been the exact re-asking-the-same-question-differently mistake from the origin story ([ADR-0001](0001-origin-non-discriminating-verification.md)).

There are dedicated domain-validation libraries (`tldextract`, etc.) that handle edge cases like public suffix lists more robustly than hand-rolled string logic.

## Decision

- Extract the host using `urlparse(url).hostname` (not `netloc`), which strips ports and credentials before comparison.
- Use `endswith(".tsmc.com")` rather than `startswith` or a plain substring check: the real domain must be the suffix, not merely present somewhere in the string.
- Do not adopt a domain-validation library. The actual requirement here is narrow (compare against a small, explicit allowlist of known company domains, not parse arbitrary internet domains correctly), and a dependency adds a thing to trust and maintain for a problem this constrained.

## Consequences

- **The real bug, and why it mattered:** the first version used `urlparse(url).netloc`, which includes the port and any embedded login credentials, not just the hostname. Adversarial review (Gemini) found a working exploit: `https://evil.com:.tsmc.com/fake-news` produces a `netloc` of `"evil.com:.tsmc.com"`, which ends with `.tsmc.com` and would pass the allowlist check, even though the real host is `evil.com`. Confirmed by actually running the exploit against the real function, not by trusting the explanation. A second review (ChatGPT) reported finding no bypass on the same code and was wrong: it tested substring spoofing thoroughly (`tsmc.com.evil.com`, `nottsmc.com`, etc.) but never tried corrupting the URL parser itself via a malformed port, a different attack surface entirely. Had only that one review been run, a function with a live exploit would have shipped alongside a report saying it was safe.
- After switching to `hostname`, the exploit URL correctly fails and resolves to `evil.com`, not `tsmc.com`.
- `endswith` defeats `tsmc.com.evil.com` (a domain that contains the real name as a prefix): `"tsmc.com.evil.com".startswith("tsmc.com")` is `True` (would wrongly pass); `"tsmc.com.evil.com".endswith(".tsmc.com")` is `False` (correctly fails). Both directions were tested before settling on `endswith`.
- Library-free approach is worth revisiting if the allowlist grows to handle truly adversarial/international domains at scale.
