# ADR-0013: Designing Bucket C — re-deriving the taxonomy and building triage

## Status

Accepted

## Context

Bucket C was originally defined as: "no single source, definitionally fuzzy. Verification: source plurality + explicit disambiguation of definition used." Before designing anything, this was re-derived from scratch against a real, concrete example already named in earlier work — TSMC's market-share claim ("roughly 60% of the foundry market") — the same discipline as every other module: build from a worked case, then check the abstraction still holds.

## Decision

- **The distinguishing test is "does a single authoritative source exist in principle," not surface wording.** The first instinct — that uncertainty-signaling language ("roughly," "probably") marks a Bucket C claim — was tested and rejected: "TSMC's revenue was roughly $90 billion last year" is hedged in identical wording yet has one precisely knowable answer from TSMC's financial statements. Revenue has one true number; "the foundry market" has no single agreed boundary (whether it includes IDMs' in-house fab capacity — Samsung, Intel — is a genuine, unresolved methodological choice, not a fact anyone is wrong about).
- **Reject a comparative/ranking-word keyword shortcut.** A deliberately constructed counterexample killed it: "TSMC is the world's largest pure-play foundry by revenue" contains exactly the kind of word the rule would flag, yet is genuinely Bucket A — "pure-play foundry" is a precisely bounded category (explicitly excluding IDMs that fab their own chips), with one knowable answer once the category is held fixed.
- **Classification requires a real LLM call, not a deterministic check.** Classifying requires reasoning about whether the underlying category is precisely bounded or genuinely contested — the same character of judgment as Bucket B's excerpt-finding.
- **`triage_claim`'s `"ambiguous"` outcome is never retried, and the function contains no retry loop at all.** Genuine bucket-classification uncertainty is structurally closer to `quote_match.py`'s `"ambiguous"` status — a stable, honest finding about the claim itself, not a transient miss re-asking would resolve — than to Bucket B's retryable `found: false`. Confirmed by a test asserting the injected LLM function is called exactly once even when it returns "ambiguous."
- **Malformed-response handling is a fourth, distinct outcome (`"malformed_llm_response"`),** never silently retried and never confused with a genuine `"ambiguous"` classification. A model failing the required JSON shape, or returning a value outside the three real options, is a format failure, not an honest opinion.
- **`reasoning` is a required field on every outcome, not just the uncertain one,** so a human reviewing why a claim was routed to Bucket A or Bucket C sees the model's actual stated reasoning, not just the label.

## Consequences

- **Malformed-handling confirmed by tests** that an out-of-vocabulary classification value (e.g. a hallucinated `"bucket_b"`) and a response missing the required `reasoning` field are both caught as malformed, not silently accepted.
- **`reasoning`-required confirmed by a test** that a response missing `reasoning` entirely, even alongside a valid `"classification"` value, is rejected as malformed rather than silently accepted with reasoning defaulting to `None`.
- **Live-verified on the exact pair of claims the design was built around:** the system prompt walks the model through the distinguishing test using the two worked counterexamples (the pure-play-foundry claim and the foundry-market-share claim) as concrete in-prompt examples — deliberately, since a model given only bare category names would be likely to fall into the keyword-shortcut trap. The live test calls the real model with no injected fake: the pure-play-foundry claim correctly routed to `"bucket_a"`, the foundry-market-share claim to `"bucket_c"`, both with real captured reasoning.
- **Status:** triage (`bucket_triage.py`) is the foundational entry point for Bucket C, now built, tested, and live-verified. Working through the design surfaced and correctly separated three genuinely distinct jobs an earlier instinct had bundled into one: triage, per-source extraction (find a claimed figure and its stated definition within one already-fetched source — see [ADR-0014](0014-source-extraction.md)), and reconciliation (grouping multiple sources by whether they share the same underlying definition, which genuinely requires judgment since two differently-worded definitions may describe the same real scope — see [ADR-0018](0018-reconciliation.md)).
