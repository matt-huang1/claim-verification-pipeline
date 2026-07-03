# Current Status

What is built, tested, and live-verified today. For how the pieces fit together, see [ARCHITECTURE.md](ARCHITECTURE.md); for what's next, see [ROADMAP.md](ROADMAP.md); for real, current gaps, see [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md).

## What's built

All four verification types are implemented, tested, and live-verified end to end:

- `src/pipeline.py` + `src/extraction.py` — Bucket A orchestration: domain check, web search, URL enforcement, page fetch, quote match with numeric token gate
- `src/bucket_b_pipeline.py` + `src/criterion_evidence.py` — Bucket B: NZIF criteria evidence gathering (all six criteria, independently verified) and TPI Management Quality extraction (all 23 indicators, direct HTML parse)
- `src/bucket_triage.py` + `src/source_extraction.py` + `src/reconciliation.py` + `src/bucket_c_pipeline.py` — Bucket C: triage, multi-source extraction, definition reconciliation
- `src/bucket_d_analysis.py` + `src/bucket_d_pipeline.py` — Bucket D: assumption and causal chain extraction
- `src/run_pipeline.py` — top-level dispatcher: routes any claim through triage to the right pipeline, consistent four-field return shape
- `src/serialisation.py` — round-trip serialisation of all evidence types to JSON
- `src/review.py` — terminal formatter for ClaimTag and pipeline result output
- `src/ground_truth.py` — primary-source verified claims and metadata for 9 companies
- `src/tpi_extract.py` — deterministic TPI Management Quality parser (raw HTML, no LLM)
- `scripts/run_batch.py` — batch runner producing `data/results.json`
- `index.html` — pre-computed results browser (serve from repo root)

**Ground truth companies:** TSMC, TotalEnergies, Patagonia, Antofagasta, Frontier Lithium, Vestas, Coal India, Cheniere, Microsoft — each chosen to test a specific structural gap in the verification system.

**Tests:** 311 passing deterministic tests. Every module has a live API test (`RUN_LIVE_API=1`) that runs against real search results, real pages, and real models — because mocked tests cannot catch the class of bug that has actually appeared in this project.

**Structured log:** `logs/evaluation_log.jsonl` — every pipeline run writes a structured entry tagged with `company_name`, `bucket`, and outcome. Used for diagnosing live failures without throwaway scripts.

## Live-verified milestones

Each of these ran against real APIs with no fixture or mock anywhere in the path. The full account of each, including the bugs the runs surfaced, is in [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md):

- **Bucket A** — the complete chain (Tavily search → model selection from real candidates → URL enforcement → real fetch → quote match) produced `"verified"` end to end on the real TSMC press release. ([details](DESIGN_DECISIONS.md#page_fetchpy--given-a-url-we-already-trust-get-its-real-content))
- **Bucket B (NZIF)** — all six criteria returned real, verified evidence for TotalEnergies on the first live run; Patagonia's run surfaced a real bot-detection limitation and produced honest gaps instead of fabricated evidence. ([details](DESIGN_DECISIONS.md#running-the-nzif-side-live-for-totalenergies-and-patagonia--a-real-diagnostic-chain-and-a-finding-that-reframed-itself-twice))
- **Bucket B (TPI)** — TotalEnergies' real Level 5 result with failing indicators 21 and 22 fetched and tagged live; Patagonia's genuine absence from TPI's universe reported specifically as `company_not_in_tpi_universe`, not a generic failure. ([details](DESIGN_DECISIONS.md#tpi_extractpy--adding-tpi-management-quality-and-a-real-architectural-fork-found-by-refusing-to-settle-for-treat-it-as-fixed))
- **Bucket C** — triage, per-source extraction, and reconciliation each live-verified individually, and the full chain live-verified through the dispatcher on the TSMC foundry market-share claim (342 seconds — the real cost of five sequential fetches plus multiple model calls). ([details](DESIGN_DECISIONS.md#run_pipelinepy--the-top-level-dispatcher-and-the-decisions-that-shaped-it))
- **Bucket D** — the TSMC counterfactual analysed live in 23 seconds with real populated assumptions and causal steps. ([details](DESIGN_DECISIONS.md#bucket_d_analysispy-and-bucket_d_pipelinepy--surfacing-reasoning-structure-for-unverifiable-claims))
