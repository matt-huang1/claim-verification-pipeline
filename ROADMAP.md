# Roadmap

What's not yet built, and work that was considered and deliberately deferred. For gaps in what *is* built, see [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md).

## What's not yet built

- Live verification wired to the browser UI (currently shows pre-computed results only) — the dispatcher and all four pipelines exist and work; the missing piece is a server layer that calls them on demand and streams progress to the browser
- Batch processing across all 9 ground-truth companies with cost management
- NZIF tier-mapping consumer (`NZIF_CRITERION_TIERS` exists in `criterion_evidence.py` but nothing reads it) — the intended consumer is the human-facing review layer, so a reviewer never mistakenly checks a criterion's evidence against a tier the framework doesn't require it for ([context](DESIGN_DECISIONS.md#the-most-consequential-bug-of-the-night-the-nzif-criteria-themselves-were-never-actually-verified))
- `target_source_count` tuning for Bucket C (currently fixed at 5; revisit once more live runs exist)

## Deliberately deferred

Each of these was considered and explicitly deferred with a stated trigger for revisiting — not overlooked. The full reasoning for each lives in [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md):

- **Table extraction / OCR in `page_fetch.py`** — real Bucket C research genuinely needs table data, but `quote_match`'s fuzzy text matching cannot use table structure at all today; building it now would be capability with no consumer. Revisit when Bucket C grows a consumer for it. ([context](DESIGN_DECISIONS.md#page_fetchpy--given-a-url-we-already-trust-get-its-real-content))
- **Async/parallel fetching** — the pipeline is synchronous and one-URL-at-a-time by design; the sync/async distinction is isolated to one module's internals, so switching later costs one function, not a restructure. ([context](DESIGN_DECISIONS.md#page_fetchpy--given-a-url-we-already-trust-get-its-real-content))
- **Database-backed log storage** — for a single-user, local project with no concurrent access, there is no present problem a database solves. Revisit at real scale. ([context](DESIGN_DECISIONS.md#unifying-logging-across-both-buckets--a-real-gap-noticed-not-invented))
- **A fetch path for bot-protected domains** — a browser-mimicking fetcher was evaluated and ruled out on ethical grounds; the right legitimate answer (e.g. a disclosed scraping agreement) depends on RBC's context. ([context](DESIGN_DECISIONS.md#running-the-nzif-side-live-for-totalenergies-and-patagonia--a-real-diagnostic-chain-and-a-finding-that-reframed-itself-twice), and [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md))
- **Bucket C search-source diversity** — query variation or a different search provider could widen the sources Tavily returns for some claims; deferred pending more live data on how common the pattern is. (See [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md).)
- **Automated Bucket D routing in triage** — Bucket D claims are usually obvious to a human reader; automated routing adds marginal benefit against a real cost at current scale. ([context](DESIGN_DECISIONS.md#bucket_d_analysispy-and-bucket_d_pipelinepy--surfacing-reasoning-structure-for-unverifiable-claims))
- **Visual-only evidence (charts) for Bucket B** — no case has yet appeared where the *only* evidence for a criterion is an image with no textual equivalent; revisit if a real company's evidence turns out to be genuinely visual-only. ([context](DESIGN_DECISIONS.md#bucket-b-verification--designed-not-yet-wired-into-a-pipeline))
- **Query-string-identified URLs in `url_compare.py`** — a real, named risk left unhandled because no real source handled so far uses query-string identification; revisit if one does. ([context](DESIGN_DECISIONS.md#url_comparepy--is-this-actually-the-url-we-were-given-or-just-one-that-looks-legitimate))
