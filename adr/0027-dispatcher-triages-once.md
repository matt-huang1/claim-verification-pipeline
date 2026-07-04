# ADR-0027: The dispatcher triages exactly once

## Status

Accepted

## Context

`run_pipeline` runs triage when no bucket is supplied, then routes. For Bucket C it delegates to `run_bucket_c_pipeline`, whose own first step is *also* triage (the C pipeline is independently callable and must be able to refuse claims that are not Bucket C). The dispatcher already injected a triage-skipping stub when the caller explicitly passed `bucket="C"` — but when the dispatcher's *own* triage routed to C, it passed the real triage function through, and the same claim was classified twice.

Two problems, one found in the same external review that produced [ADR-0026](0026-search-unavailability.md):

1. **Duplicate cost.** Every triage-routed Bucket C claim paid for two identical LLM classification calls.
2. **Contradictory routing.** Triage is nondeterministic. The second call could return `bucket_a`, producing the incoherent result `{"bucket": "C", "outcome": "routed_to_bucket_a"}` — the dispatcher asserting one routing decision and its sub-pipeline another, in the same response.

## Decision

Whenever the dispatcher routes to Bucket C — by its own triage *or* by an explicit `bucket="C"` — it injects a stub that replays the routing decision already made (carrying the dispatcher-level triage reasoning, or `"explicitly routed by caller"`). `run_bucket_c_pipeline` keeps its triage step unchanged for direct callers; the dispatcher simply never asks the same question twice.

Rejected alternative: removing triage from `run_bucket_c_pipeline` and hoisting it entirely into the dispatcher. That would make the C pipeline unable to defend its own precondition when called directly — its triage gate exists precisely so a non-C claim handed to it is refused rather than force-fit ([ADR-0013](0013-designing-bucket-c.md)).

## Consequences

- One triage call per dispatched claim, always. A regression test counts the calls.
- `{"bucket": "C", "outcome": "routed_to_bucket_a"}` is no longer a reachable state through the dispatcher.
- `triage_reasoning` in the C pipeline's log/result now consistently reflects the decision that actually routed the claim.
