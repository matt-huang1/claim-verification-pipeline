# Architecture Decision Records — Index

This is the index of Architecture Decision Records (ADRs) for this project. The [README](README.md) tells a stranger what the system does; these records document *why* each decision was made, including the approaches that were tried and rejected — the full reasoning behind any single line, kept so every decision can be defended unprompted. For the resulting structure, see [ARCHITECTURE.md](ARCHITECTURE.md); for current gaps, see [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md).

The core thesis behind every decision recorded here: **a check only counts if it would have given a different answer in the world where the claim was false.** A check that says "looks fine" regardless of truth is not a check, it is theater. This is called **non-discriminating verification** throughout, and it is the thing every fix below was correcting for.

Each record follows the ADR structure — **Context**, **Decision**, **Consequences** — and lives as an individual file under [adr/](adr/). No information from the original design record has been dropped; the records preserve the full reasoning, every rejected alternative, and every real bug found in live runs.

## Records

| # | Record | Summary |
|---|--------|---------|
| 0001 | [Founding principle — non-discriminating verification](adr/0001-origin-non-discriminating-verification.md) | The failure that started this and the thesis it produced |
| 0002 | [domain_check.py](adr/0002-domain-check.md) | Is this URL actually who it claims to be |
| 0003 | [quote_match.py](adr/0003-quote-match.md) | Does this quote actually appear in this document |
| 0004 | [tag_schema.py](adr/0004-tag-schema.md) | What "verified" means and who is allowed to say it |
| 0005 | [pipeline.py](adr/0005-pipeline.md) | Bucket A wiring, deliberately kept thin |
| 0006 | [extraction.py](adr/0006-extraction.md) | The one place a real model gets called |
| 0007 | [page_fetch.py](adr/0007-page-fetch.md) | Given a URL we already trust, get its real content |
| 0008 | [url_compare.py](adr/0008-url-compare.md) | Is this actually the URL we were given |
| 0009 | [Bucket B verification](adr/0009-bucket-b-evidence-structure.md) | The evidence-structure design |
| 0010 | [criterion_evidence.py](adr/0010-criterion-evidence.md) | The project's thesis demonstrated on itself |
| 0011 | [tpi_extract.py](adr/0011-tpi-extract.md) | TPI Management Quality extraction |
| 0012 | [NZIF live for TotalEnergies and Patagonia](adr/0012-nzif-live-totalenergies-patagonia.md) | A real diagnostic chain and a named limitation |
| 0013 | [Designing Bucket C](adr/0013-designing-bucket-c.md) | Re-deriving the taxonomy and building triage |
| 0014 | [source_extraction.py](adr/0014-source-extraction.md) | Bucket C per-source extraction |
| 0015 | [bucket_b_pipeline.py](adr/0015-bucket-b-pipeline.md) | The Bucket B orchestrator |
| 0016 | [Unifying logging across both buckets](adr/0016-unified-logging.md) | A real gap noticed, not invented |
| 0017 | [Cross-cutting lessons](adr/0017-cross-cutting-lessons.md) | Lessons that shaped multiple decisions |
| 0018 | [reconciliation.py](adr/0018-reconciliation.md) | Bucket C reconciliation |
| 0019 | [bucket_d_analysis.py and bucket_d_pipeline.py](adr/0019-bucket-d-analysis-and-pipeline.md) | Reasoning structure for unverifiable claims |
| 0020 | [run_pipeline.py](adr/0020-run-pipeline.md) | The top-level dispatcher |
| 0021 | [serialisation.py, run_batch.py, and index.html](adr/0021-results-layer.md) | The results layer |
| 0022 | [Known open limitations](adr/0022-known-open-limitations.md) | Real, current gaps, named explicitly |
