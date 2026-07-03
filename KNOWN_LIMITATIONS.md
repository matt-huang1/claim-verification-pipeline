# Known Limitations

These are real, current gaps — named here rather than left as implicit TODOs. Deferred *future work* (as opposed to gaps in what's built) is tracked in [ROADMAP.md](ROADMAP.md).

## Open limitations

**Patagonia bot-detection:** Patagonia's website actively blocks plain HTTP fetches, returning a holding page rather than real content. The system correctly returns `not_found_after_retries` for every NZIF criterion rather than fabricating evidence. The right fix (a browser-capable fetcher, or a disclosed scraping agreement) was explicitly deferred rather than patched reflexively, because the reusable, unattended-scale version of any bypass matters and the right answer depends on RBC's context. ([full account, including the ethical line drawn](DESIGN_DECISIONS.md#running-the-nzif-side-live-for-totalenergies-and-patagonia--a-real-diagnostic-chain-and-a-finding-that-reframed-itself-twice))

**Bucket C source diversity:** For the TSMC foundry market share claim, Tavily consistently returns results dominated by a single domain (`averroes.ai`). After URL deduplication, this means reconciliation receives only one unique source — not enough to establish a group. The honest result (`definitional_ambiguity_unresolved`) is correct. The underlying issue is Tavily's search result diversity for this specific claim, not a code defect. Addressable by query variation or a different search provider; deferred pending more live data on how common this pattern is across other Bucket C claims.

**`target_source_count` for Bucket C:** Currently fixed at 5 at the orchestrator level. The 248-second live run suggests this may be worth reducing — but one run isn't enough data. Revisit after several runs across different claims, using the structured log to see how many of the 5 actually contributed usable findings.

**Live verification not wired to the UI:** `index.html` shows pre-computed results only. The dispatcher (`run_pipeline.py`) and all four pipelines exist and work; the missing piece is a server layer that calls them on demand and streams progress to the browser.

## Scope limits stated at the module level

Each of these is a deliberately drawn boundary, documented where the decision was made rather than implied away:

- **The numeric token gate only catches numeric hallucinations.** "The board REJECTED the proposal" against a source saying "APPROVED" would still pass — semantic/antonym detection is a real, separate, harder problem that wasn't attempted, and the docstring says so explicitly. ([details](DESIGN_DECISIONS.md#the-numeric-token-gate--the-single-most-important-fix-in-the-whole-project))
- **`page_fetch.py` extracts plain text only** — HTML and clean, digitally-created PDFs. No table extraction, OCR, or scanned-document handling. ([details](DESIGN_DECISIONS.md#page_fetchpy--given-a-url-we-already-trust-get-its-real-content))
- **`url_compare.py` strips query strings before comparing**, so two articles distinguished only by a query-string identifier would wrongly be treated as the same page. No real source handled so far uses query-string identification; revisit if one does. ([details](DESIGN_DECISIONS.md#url_comparepy--is-this-actually-the-url-we-were-given-or-just-one-that-looks-legitimate))
- **`tpi_extract.py`'s page structure is confirmed for exactly one company's page** (TotalEnergies). An unexpected indicator count or class value returns a distinct, honest failure rather than a confidently-wrong partial parse. ([details](DESIGN_DECISIONS.md#tpi_extractpy--adding-tpi-management-quality-and-a-real-architectural-fork-found-by-refusing-to-settle-for-treat-it-as-fixed))
- **Bucket B evidence is text-only.** No case has yet appeared where a criterion's only evidence is a chart with no textual equivalent; the gap is named, not solved preemptively. ([details](DESIGN_DECISIONS.md#bucket-b-verification--designed-not-yet-wired-into-a-pipeline))
- **The results browser shows pre-computed results, which can go stale** when company disclosures change — an accepted tradeoff until live verification is wired to the UI. ([details](DESIGN_DECISIONS.md#serialisationpy-run_batchpy-and-indexhtml--the-results-layer))
