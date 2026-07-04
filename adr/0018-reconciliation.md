# ADR-0018: reconciliation.py - grouping sources by shared definition

## Status

Accepted

## Context

Given the list of `SourceFinding`s already gathered and verified by `source_extraction.py`, `reconciliation.py` groups the definition-bearing ones by whether they share the same underlying real-world scope - even when worded differently - and produces a `SourcePluralityEvidence` that accounts for every input source exactly once across five named slots.

The original placeholder (`sources_checked: int`, `definitions_reconciled: bool`, `notes: str`) was built before reconciliation existed and could not represent what reconciliation actually needed to say.

## Decision

Three decisions drove the `SourcePluralityEvidence` redesign, each tested against a concrete wrong answer:

- **Sources with no stated definition get their own named slot, kept visible but not grouped.** Three options: exclude them (loses real verified evidence), treat "no definition" as its own group (manufactures false equivalence between sources that merely share an absence), or keep them visible in their own slot without grouping. The third was the only one that did not either lose information or misrepresent it, at the cost of changing the dataclass now rather than later. That cost was accepted.
- **Three outcomes per definition-bearing source, not two:** grouped with others sharing a scope, confidently distinct (its own real scope, clearly different), or unresolved (relationship genuinely unclear). "Distinct" and "unresolved" are different findings - one says something positive about what a source means, the other says the relationship could not be determined - and collapsing them into a shared "singleton" label would lose exactly the distinction the redesign was built to preserve.
- **Confidently-distinct singletons live in a separate `distinct_sources` list, not inside `groups` as 1-member entries.** Any code reading `groups` later would have to remember to check size before treating "grouped" as "multiple sources agree," and a forgotten check would silently present a single unconfirmed source with the same structural weight as a real multi-source consensus. A separate list forces the distinction into the type itself.
- **`failed_reconciliation` is a fifth, separate slot, not folded into `unresolved`.** "Unresolved" is a genuine judgment outcome (the model read the definitions and concluded the relationship was unclear); "failed_reconciliation" means the model never produced a usable response at all after retries. A reviewer needs to tell these apart: one is informative about the sources, the other about whether reconciliation ran.
- **One whole-list LLM call, not pairwise comparisons.** Pairwise comparisons risk intransitive verdicts (A grouped with B, B grouped with C, A distinct from C) - a contradiction a batch call cannot produce because the model sees all definitions simultaneously and must produce a globally consistent assignment. Once the three-outcome-per-source design was settled, pairwise-then-stitch became strictly harder: "unresolved" edges are not transitive and not symmetric, so stitching N² pairwise verdicts into a coherent group structure would itself be a second judgment pass.
- **Retry malformed responses only, cap of 2.** A malformed JSON response is a formatting slip retrying can plausibly fix; a well-formed "all unresolved" verdict is a stable, honest finding retrying would not improve. Cap of 2 total attempts - small enough to be cheap, large enough to catch a one-off glitch.
- **`overall_status` rule:** `disambiguated` if `len(groups) >= 1` (at least one real, multi-source consensus exists), `definitional_ambiguity_unresolved` otherwise. The "otherwise" case deliberately covers both "all sources unresolved" and "all sources confidently distinct with no agreement" under the same label, because in both cases the original claim has no supportable consensus value. The distinction between them stays fully visible in `distinct_sources` vs `unresolved`.
- **Logging:** one entry per `reconcile_sources` call, written after the result is fully constructed, never inside the retry loop. `outcome` is one of four named strings (`reconciled`, `no_definitions`, `sole_source`, `failed`), `attempts_made` tracks whether a retry was needed, and per-slot counts give enough to diagnose any real live failure. `company_name` is a required, keyword-only parameter - the "never inferred, always explicit" rule for the third time, after `extract_claim_evidence` and `run_bucket_b_pipeline`.

## Consequences

- **Retry behaviour confirmed** by a test asserting the LLM is called exactly once on a well-formed all-unresolved response.
- **The all-distinct case locked in** by a test (`test_bucket_c_all_distinct_no_group_is_definitional_ambiguity_unresolved`), since "all distinct" is the non-obvious case that could plausibly have been special-cased.
- **Live-verified result:** `test_live_reconcile_worked_examples` passed on first run in 16 seconds - Sources A and B grouped, C distinct, D unresolved, nothing in `failed_reconciliation`, using the exact four worked-example definitions from the system prompt as real `SourceFinding` inputs with no fixtures anywhere in the chain.
