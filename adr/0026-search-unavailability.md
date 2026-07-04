# ADR-0026: Search unavailability is a named failure, not an empty result set

## Status

Accepted

## Context

`web_search.search_for_source` originally returned an empty list on *any* failure: a missing `TAVILY_API_KEY`, an auth or quota error, a network failure — all indistinguishable from a search that ran and genuinely found nothing. Every consumer treated the empty list as the named state `no_search_results` and retried or reported accordingly.

An external principal-level review of the repository caught the consequence: **a configuration error produced honest-looking verification outcomes.** Run the pipeline with no API key and Bucket A reports `unverifiable_after_retries`, Bucket B reports an `incomplete` tag, and Bucket C reports `definitional_ambiguity_unresolved` — each a legitimate-sounding verdict about the *claim*, when the truth is a verdict about the *environment*. This directly contradicts the project's own stated principle ([ARCHITECTURE.md](../ARCHITECTURE.md), "Failures are named, never collapsed") and, worse, the founding thesis: a check that reports "unverifiable" identically in the world where the claim has no sources and the world where the key is missing is non-discriminating about its own infrastructure.

## Decision

- **`search_for_source` raises `SearchUnavailable`** for a missing key (before any client is constructed) and for any client/API exception (wrapping the cause). An empty list now means exactly one thing: the search ran and found nothing.
- **The failure is a property of the infrastructure, not the claim, so no retry loop applies.** Bucket A's extraction loop logs one `search_unavailable` attempt and returns the terminal status `search_unavailable` immediately — retrying the same claim cannot fix the configuration.
- **Partial evidence survives; zero evidence propagates.** Buckets B and C stop searching when unavailability strikes mid-run: evidence already gathered is real and is returned as a partial result (the failure still logged). If *nothing* was gathered, the exception propagates so the caller reports `search_unavailable` rather than an `incomplete`/`unresolved` tag that would look like an honest no-evidence outcome.
- **The dispatcher passes `search_unavailable` through as a named outcome** for Buckets A, B, and C — never collapsed into `unverifiable` or `incomplete`.

Rejected alternative: a sentinel return value (e.g. `None` vs `[]`). Every consumer would need to branch on a second "empty-like" value, and a forgotten branch silently reproduces the original bug. An exception cannot be ignored by accident.

## Consequences

- A fresh clone run without keys now fails loudly with `search_unavailable` at every level (log entry, extraction status, dispatcher outcome) instead of producing plausible verdicts.
- The `stage_reached` vocabularies in `extraction.py` and `bucket_b_pipeline.py` gain `search_unavailable`; `bucket_c_pipeline.py` gains a sixth outcome shape.
- `run_bucket_b_pipeline` and `gather_source_findings` can now raise — documented in their docstrings, caught by their orchestrators, and asserted by tests for both the zero-evidence (raise) and partial-evidence (return) paths.
- The mid-run partial rule is a judgment call: evidence verified before the outage is not invalidated by it. `sources_checked` and the per-criterion log entries keep the record honest about how far the run got.
