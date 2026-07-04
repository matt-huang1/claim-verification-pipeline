# ADR-0014: source_extraction.py - Bucket C per-source extraction

## Status

Accepted

## Context

Bucket C per-source extraction, built and live-verified on the real TSMC market-share claim. Three separate design questions had to be settled before any code was written, each with a real wrong answer considered and rejected first.

## Decision

- **A new orchestrator (`gather_source_findings`), built from existing individual functions, not a reuse of Bucket A/B's loop.** The individual mechanics (`search_for_source`, `same_url`, `fetch_page_text`) are genuinely reusable, but the looping logic is not - Bucket A and B's loops retry the same step hoping for one better result and stop on first success; Bucket C needs a loop that deliberately keeps going to accumulate several independent results for the same claim. Same building-block reuse already proven in `bucket_b_pipeline.py`.
- **A fixed target count (`target_source_count`, default 5), not an agreement-based early stop.** The appealing "stop once sources agree, keep going if they disagree" answer would require running reconciliation logic inside the gathering loop, before reconciliation itself had been designed - inverting the sequencing every other module followed. Documented as a starting assumption with the same character as `NO_PROGRESS_SCORE_DELTA`, free to revisit once real runs exist.
- **`is_literal_value` reframed as a mechanical, form-based question.** If framed as "does this sound vague," the same hedge-word trap as `bucket_triage.py` applies. Reframed as "is there a literal digit in the text, regardless of any hedge word nearby," which a model cannot be misled on by surface phrasing - "roughly 60%" correctly evaluates to `is_literal_value=True` (a literal "60%" is present) despite the hedge word. The system prompt states this distinction explicitly, mirroring `bucket_triage.py`'s worked-counterexample design.
- **`SourceFinding` has two independent honest-absence fields (`value_found`, `definition_found`), each verified by its own separate `quote_match` call.** A source can state a value without a definition, a definition without a value, both, or neither - four genuinely independent states. Each proposed piece is verified by its own `match_quote` call against the real document, never trusted on the model's word, so a `SourceFinding` can honestly hold a verified value alongside a failed definition, or the reverse.
- **The floor rule:** a `SourceFinding` is only included if at least one of its two fields independently achieved a `"unique"` `quote_match` result - a source contributing neither a verified value nor a verified definition is noise, omitted rather than placeholded.
- **`allowlist` populates `source_type` honestly, never gates or rejects a result.** Bucket C's character is different by definition - there is no single authoritative source, so legitimate evidence is expected to come from third-party analyst and research houses, not the claim subject's own domain.

## Consequences

- **A test gap the floor rule surfaced:** the first build tested the floor rule in one direction only (a fabricated value paired with a verified definition); the mirror case (a verified value paired with a fabricated definition) had no equivalent test - a real, asymmetric gap, since `value_verification_status` and `definition_verification_status` are computed by genuinely separate code paths, so a bug specific to one direction would not necessarily be caught by a test of the other. Closed by adding the missing mirror test before treating the floor rule as proven in both directions.
- **The allowlist question answered by real evidence:** the live test confirmed all three real sources gathered for TSMC's market-share claim came back `"third_party"`, exactly the expected, correct outcome for a definitionally contested claim - not a partial failure, and not something to engineer around.
- **Live-verified end to end,** with no fixtures anywhere in the path: real Tavily search, real OpenAI URL selection, real HTTP fetch, real OpenAI per-source extraction, real `quote_match` verification, on the exact claim ("TSMC has roughly 60% of the foundry market") this Bucket C design was built around.
- **Status:** all three Bucket C pieces - triage, per-source extraction, and reconciliation - are built, tested, and live-verified. See [ADR-0018](0018-reconciliation.md) for reconciliation.
