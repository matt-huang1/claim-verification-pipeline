# ADR-0005: pipeline.py — Bucket A wiring, deliberately kept thin

## Status

Accepted

## Context

`pipeline.py` is the wiring that composes the Bucket A checks into a single verify-one-claim operation. The design question is how to split responsibilities so the composed behaviour is both testable in isolation and reusable for later buckets.

## Decision

A three-function split:

- `run_bucket_a_checks(...)` — calls the two real checks, returns raw results. The only function that touches real checks.
- `build_bucket_a_tag(...)` — wraps raw results into typed evidence, builds the `ClaimTag`. Testable instantly with hand-built fake inputs, no real checks needed (mirrors exactly how `tag_schema.py`'s own tests were built).
- `verify_bucket_a_claim(...)` — thin wrapper calling 1 then 2. It exists despite 1 and 2 doing the real work because it is the "recipe," not "extra flexibility" — the actual flexibility comes from 1 and 2 already being separate. The recipe exists because the common real-world task (verify one claim, get one tag) deserves a one-call shape, while the techniques underneath stay separately testable and reusable for whatever Bucket B/C/D pipelines eventually need a different combination of steps.

## Consequences

- **Keystone end-to-end test:** a real quote match (unique, perfect score) paired with a spoofed domain → `overall_status == "source_illegitimate"`. Proves the refusal happens at the composed pipeline level, not just inside isolated units.
