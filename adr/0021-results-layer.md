# ADR-0021: serialisation.py, run_batch.py, and index.html — the results layer

## Status

Accepted

## Context

This layer converts `ClaimTag` objects and `run_pipeline` result dicts to plain JSON (and back), runs a curated set of claims through the pipeline in batch, and displays the results in a single-file browser.

## Decision

- **Serialisation needs its own module rather than `json.dumps` directly.** `ClaimTag` contains nested frozen dataclasses with non-JSON-native types — `dict[int, str]` for TPI indicator results (JSON keys must be strings), `list[tuple[str, int]]` for historical levels (JSON has no tuple type), and computed properties (`overall_status`) that do not appear in `__dict__`. A dedicated `tag_to_dict` / `dict_to_tag` round-trip ensures every consumer gets a consistent shape without each one reimplementing the coercion logic. `overall_status` is explicitly included in the serialised form so the UI can display it without reconstructing the full dataclass — it is deterministic, so storing it is safe.
- **Pre-computed results over live UI calls.** Bucket C runs take 248 seconds; Bucket D takes 23 seconds; a full portfolio would take over an hour. Displaying pre-computed results avoids blank-screen latency entirely, keeps API costs predictable, and means the UI is demonstrable without any backend running. The tradeoff is staleness — results do not update when company disclosures change. Accepted for the current build; revisit when live verification is wired to the UI.
- **Deduplicate findings by `source_url` at return** (see Consequences).
- **`index.html`: single-file, no build step, no framework.** Fetches `data/results.json` at load time via `fetch()`. Two-panel layout: sidebar with tab-switching between "New claim" (form, disabled pending live integration) and "Results" (browsable list), main panel showing detail or the search home. The "New claim" tab shows the full form — company, claim, advanced type selector — even though submission is disabled, so the intended workflow is legible to a reviewer seeing the tool for the first time. Advanced options hidden behind a toggle to keep the primary flow clean. The NZIF criteria matrix shows verbatim definitions and a tier-by-criterion grid for listed equity and corporate fixed income, with a footnote that governance applies to other asset classes — accurate to the framework without overstating what the current evidence set covers.

## Consequences

- **The URL deduplication fix, found during the first real batch run:** `gather_source_findings` already deduplicates HTTP fetches via an in-call cache, but multiple loop iterations could still select the same URL, hit the cache, and produce multiple `SourceFinding` objects with the same `source_url`. When all 5 findings from a single batch run came from `averroes.ai`, reconciliation received 4 identical source URLs, correctly grouped them into one group, but `_validate_response` correctly rejected a 1-member group. The fix: deduplicate findings by `source_url` at return, keeping the first per URL. The existing fetch cache handles the HTTP efficiency; the new deduplication handles the reconciliation input correctness. Two complementary mechanisms with different jobs, confirmed necessary by a real live failure.
