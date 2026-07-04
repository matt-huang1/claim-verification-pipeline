# ADR-0016: Unifying logging across both buckets

## Status

Accepted

## Context

After Bucket B reached live parity with Bucket A, it became clear that `bucket_b_pipeline.py` and `criterion_evidence.py` had no logging mechanism at all - confirmed by `grep`, not assumed. This was the exact reason every Bucket B live-run diagnosis that night required manually instrumenting the code by hand: there was no structured record of what had actually happened, only `extraction.py`'s Bucket A log existed. The problem was not underused logs - simply absent logs for half the system.

## Decision

- **Do not connect to a database now.** "Connect this to a database for security" was tested against the same question every infrastructure suggestion got: is there an actual, present problem this solves, or is it borrowing concern from a future scale the project is not at? For a single-user, local project with no concurrent access and no authentication boundary, there is no real security gap a database would close right now. Deferred explicitly, the same shape as deferring async fetching or table extraction.
- **One log entry per criterion, not per internal sub-step.** Bucket A's `AttemptRecord` (one entry per retry attempt with a `stage_reached` field) had already proven sufficient for diagnosing every Bucket A live issue without finer sub-step logging. The same shape is reused for Bucket B: one entry per criterion attempted, carrying `stage_reached` values specific to this module's real chain (`no_search_results`, `url_not_from_search_results`, `fetch_failed`, `excerpt_not_verified`, `excerpt_verified`).
- **One shared log file, not separate files per bucket.** The stated goal was cross-bucket, cross-company history ("what is more common, what went well, for which types"), fundamentally a question about one company's whole picture. Separate files would mean manually merging two files every time; one shared file with explicit `bucket` and `company_name` fields on every entry answers it directly. The shared "append a JSON line" logic was extracted into its own tiny module (`log_utils.py`) rather than duplicated in both callers - the same judgment as sharing `quote_match.py`: when two callers need the exact same deterministic operation, duplicating it risks silent divergence.
- **`company_name` became a new required parameter on `extract_claim_evidence`, not optional or inferred.** `bucket_b_pipeline.py` had already settled this exact question (company name explicit, never derived, since a misparse would silently contaminate every downstream query). Applying the identical reasoning to `extraction.py` meant a real, somewhat invasive change to an already-large, working, heavily-tested public function.

## Consequences

- The `extract_claim_evidence` change touched eighteen existing test call sites, all needing the new argument, done as a deliberate mechanical pass with the full suite re-run afterward specifically because a new required parameter on a function this central could plausibly break something far from the logging change.
- **A bug this change caught in itself, found only by running the live tests:** mid-refactor, the `import json` statement in `extraction.py` was removed while migrating to the shared `log_utils.append_log_entry` helper - but `_default_llm_call`, which parses the real OpenAI response, still depended on it. The full deterministic suite (141 tests) passed throughout, because not one exercises the real `_default_llm_call` path; every one injects a fake `llm_fn`. Only the live test surfaced the break, producing two consecutive `malformed_llm_response` failures that, on inspection of the real log, pointed straight at the missing import. The same lesson as `page_fetch.py`'s chunked-encoding discovery and `bucket_b_pipeline.py`'s search-query bug, a third time: a green deterministic suite proves the code's logic is internally consistent with its own assumptions, not that those assumptions - or a refactor's own completeness - were actually correct. Found, diagnosed, and fixed within the same session, before the change was committed.
