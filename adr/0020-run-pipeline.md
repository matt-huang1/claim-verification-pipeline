# ADR-0020: run_pipeline.py — the top-level dispatcher

## Status

Accepted

## Context

`run_pipeline.py` takes a claim text, an allowlist, a company name, and a claim ID, runs triage to route the claim to the right bucket pipeline, and returns a consistent dict with four fields (`outcome`, `bucket`, `triage_reasoning`, `tag`) regardless of which pipeline ran or whether triage failed.

## Decision

- **Bucket B never comes from triage.** Triage distinguishes three structural categories — claims with a single authoritative source (Bucket A), definitionally contested claims (Bucket C), and uncheckable claims (Bucket D). Bucket B covers a company's alignment with a specific external framework (NZIF), which requires a human to identify which framework applies and which criteria to check — triage has no basis for making that determination from claim text alone. Bucket B always requires explicit `bucket="B"` from the caller.
- **The return shape is always a dict with four fields, not a ClaimTag directly.** `bucket_b_pipeline.py` and `bucket_d_pipeline.py` return a ClaimTag directly because they have one real outcome; the dispatcher has triage failure paths that produce no tag at all. A consistent return shape means every consumer checks `result["outcome"]` and `result["tag"]` without knowing or caring which pipeline ran — one contract for a future front end.
- **Bucket A's ClaimTag is built via capturing closures.** `extract_claim_evidence`'s return dict contains `status`, `attempts`, and `last_attempt_status` — no URL, quote, or ClaimTag. To build a ClaimTag on a "verified" outcome, `_run_bucket_a` wraps the `llm_fn` and `fetch_fn` passed to `extract_claim_evidence` with capturing closures that record the last URL and quote proposed by the LLM and the last successfully fetched document. On a "verified" return, these values are guaranteed to belong to the successful attempt because `extract_claim_evidence` returns immediately on first success. The captured values are then passed to `verify_bucket_a_claim` to build a real ClaimTag. No log reading required. A public wrapper `default_llm_call` was added to `extraction.py` so `run_pipeline.py` can reference the real LLM call without accessing a private symbol — the only architectural coupling between the two modules is now through that public interface, not through a name-prefixed private function.
- **Triage is skipped when `bucket` is explicitly supplied.** An explicit bucket is a human-supplied routing decision that overrides the model's judgment; calling triage anyway would waste cost and could produce a contradictory routing. Skipping it also guarantees `triage_llm_fn` is never called in tests that supply `bucket=` explicitly — a testability property, not just efficiency.

## Consequences

- **Live-verified result:** two real API calls, both passed — Bucket C on "TSMC has roughly 60% of the foundry market" (full triage + gathering + reconciliation chain, 342 seconds), and Bucket D on the TSMC counterfactual with explicit routing (23 seconds). The 342-second Bucket C run is the expected cost of five real HTTP fetches and multiple OpenAI calls in sequence — not a bug, the cost of genuine verification.
