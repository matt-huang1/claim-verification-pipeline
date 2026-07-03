# Agent Evaluation Framework

A verification layer for AI-assisted climate transition research at RBC. Its job is to catch errors before they reach client documents — specifically, cases where a model produces plausible-sounding claims not grounded in primary sources.

**The founding failure:** an earlier AI-assisted assessment of TSMC used a framework classification label that does not exist in the IIGCC NZIF 2.0 source document. It was caught by going back to primary sources, not by asking the model to check itself. This system exists to make that check systematic.

## How it works

Not all claims can be verified the same way. The system classifies each claim and applies the right kind of check:

| Type | What it covers | How it's checked |
|---|---|---|
| **Source verification** | A single authoritative source exists — a press release, a TPI score, a regulatory filing | Deterministic: domain check + quote match against the real document |
| **Framework alignment** | Judgment against NZIF or TPI criteria | Evidence gathered for human review — never automated |
| **Definition disambiguation** | Definitionally fuzzy claims (e.g. market share) where "the answer" depends on scope | Multiple sources gathered, definitions reconciled |
| **Reasoning transparency** | Counterfactual or forward-looking claims, uncheckable by definition | Assumptions and causal chain surfaced for human review |

The system never decides whether a claim is "good enough." That judgment belongs to a human. What it does is gather, verify, and structure the evidence so the human decision is grounded in primary sources, not in what the model thinks it remembers.

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

## Running the results browser

```
python scripts/run_batch.py      # run pipeline on curated claims, writes data/results.json
python -m http.server 8080       # serve from repo root
# open http://localhost:8080
```

## Development

```
pip install -r requirements.txt
cp .env.example .env                        # add OPENAI_API_KEY and TAVILY_API_KEY
python -m pytest -m "not live_api" -q      # full deterministic suite
RUN_LIVE_API=1 python -m pytest -m live_api -v   # live tests (cost real API calls)
python -m black --check src/ tests/        # formatting check
python -m flake8 src/ tests/               # lint
```

## What's not yet built

- Live verification wired to the browser UI (currently shows pre-computed results only)
- Batch processing across all 9 ground-truth companies with cost management
- NZIF tier-mapping consumer (`NZIF_CRITERION_TIERS` exists in `criterion_evidence.py` but nothing reads it)
- `target_source_count` tuning for Bucket C (currently fixed at 5; revisit once more live runs exist)

See `DESIGN_DECISIONS.md` for the full design trail, including every rejected alternative and every real bug found in live runs.