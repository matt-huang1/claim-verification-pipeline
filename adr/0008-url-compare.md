# ADR-0008: url_compare.py — is this the URL we were given

## Status

Accepted

## Context

`url_compare.py` provides `same_url(url_a, url_b)` — compares two URLs, tolerating trivial formatting differences (scheme, a leading `www.`, a single trailing slash) while treating the path as exact.

The gap it closes, found by reviewing the search integration rather than by an adversarial test: `extraction.py`'s system prompt tells the model to "only use URLs from the provided candidates list," but nothing in the code checked this. `verify_bucket_a_claim` only confirms a proposed URL passes `check_domain`'s allowlist — it has no way to know whether that URL was actually one of the real search candidates, or one the model generated from memory that happened to land on an allowed domain. A model quietly ignoring the prompt instruction would be indistinguishable, downstream, from one genuinely grounded in search results — silently reducing the entire protection search was added to provide to "as strong as the model's willingness to follow an instruction," relocated one level up.

## Decision

- **Neither a hard exact-string match nor a fuzzy similarity score.** A hard match is too brittle — a model that correctly identifies the right page can still write the URL with a trivial difference (trailing slash, `http` vs `https`) that an exact match would wrongly reject. But `quote_match.py`'s fuzzy-similarity approach is the wrong tool too, deliberately not reused: text tolerates approximate matching because meaning survives small wording differences; a URL path does not — a single differing character can point to a genuinely different document, not a paraphrase. The fix normalizes only the genuinely cosmetic parts (scheme, `www.`, trailing slash) and keeps the path comparison exact after normalization.
- **Its own module rather than inside `extraction.py`.** The operation "do these two URLs point to the same page" is a distinct, narrow, independently reusable question. Any future Bucket B/C/D verification that needs the same comparison should not have to duplicate this normalization logic. Same reasoning as `page_fetch.py`'s separation from `extraction.py`.
- **`_normalize` strips query strings and URL fragments entirely before comparing,** on the reasoning that they are usually tracking parameters (`?utm_source=...`) that should not cause two URLs pointing at the same real page to be treated as different.

## Consequences

- **A real, named risk deliberately left unhandled:** some real content-management systems use a query-string parameter as the actual article identifier (e.g. `/article?id=3067` vs `/article?id=3068` being two genuinely different documents), which would mean two different real articles get wrongly treated as the same URL under the current logic. This is a real, plausible risk, not a dismissed one. Checked against every actual URL this project has touched (TSMC's press releases, sustainability report, every search-result URL handled in testing) — all path-based (`/english/news/3067`), never query-string-based for identification. Decision: leave the limitation documented rather than tighten the comparison now — a stricter comparison would start rejecting trivial, genuinely harmless tracking-parameter differences this function was built to tolerate, a real cost against a risk that has not shown up in any real source yet. Revisit if a real source ever does use query-string-based identification — not before.
