# Agent Evaluation Framework

A system for evaluating whether an AI-assisted research pipeline produces *trustworthy* output, not just *plausible-sounding* output, built around a real, narrow use case: assessing companies as climate transition enablers against the IIGCC NZIF 2.0 and TPI frameworks.

**The eval layer is the product. The agent that does the research is not the point.**

## Why this exists

This project started from a concrete failure, not a hypothetical one. An earlier, manually-produced assessment of a real company (TSMC) used AI-assisted research, and a verification step that *felt* rigorous (asking the same model to double-check itself, multiple times, in the same conversation) let a real, sourced error through: a framework classification label that does not actually exist in the source document it was supposedly drawn from. The error was eventually caught, but only by going back to primary sources directly. The "verification" performed before that point had no actual power to catch it.

That failure mode has a name in this project: **non-discriminating verification**, a check that would say "looks fine" whether or not the underlying claim is true, because it has no independent access to the truth. Re-asking the same model is the clearest example. Most of this project exists to design checks that don't have that defect.

## Core design idea: not all claims can be verified the same way

Claims in a transition-enabler assessment fall into different categories depending on what kind of ground truth, if any, exists for them:

| Bucket | What it means | How it's actually checked |
|---|---|---|
| **A** | A single authoritative source exists (a company's own press release, a published TPI score) | Deterministic: does the extracted claim match the actual source document, exactly |
| **B** | A judgment call, but anchored to checkable present facts (e.g. which NZIF alignment tier a company sits in) | Not "is it true", but: is the reasoning shown, and is the underlying technical classification correct |
| **C** | No single source, because the question is definitionally fuzzy (e.g. market share, which depends on category definitions) | Source plurality, plus explicit disambiguation of which definition is in use |
| **D** | Future-facing or counterfactual, no fact-check possible even in principle | Are the assumptions stated and the causal chain explicit |

A system that applies the same kind of check to all four buckets will either over-verify cheap facts or under-verify genuine judgment calls. This project treats bucket classification as a precondition for choosing a verification strategy, not an afterthought.

## What's built: a complete, live-verified Bucket A pipeline

The repo contains a fully-specified, fully-tested, end-to-end pipeline for Bucket A claims, from a plain-English claim to a verified record, **run live with no fixtures or mocks anywhere in the chain**:

1. **Domain legitimacy check** (`src/domain_check.py`) — does the claimed source URL actually belong to the company, checked against an allowlist, not a third-party cross-reference (a self-announcement doesn't need corroboration; it needs confirmation it came from the right place).
2. **Web search** (`src/web_search.py`) — finds real candidate URLs for a claim (Tavily, basic search only, no bundled content extraction) so the model selects from real sources rather than generating a plausible-looking URL from memory.
3. **URL match check** (`src/url_compare.py`) — confirms the model's selected URL actually came from the real search results, tolerating trivial formatting differences (scheme, `www.`, trailing slash) but treating the path as exact. A prompt instruction alone ("only use the provided URLs") is not enforcement; this is.
4. **Page fetch** (`src/page_fetch.py`) — retrieves the real content of the selected URL (HTML or clean digital PDF), with honest, specific failure reporting (timeout, 404, oversized, unsupported format) and no retry logic of its own.
5. **Quote match check** (`src/quote_match.py`) — does the claimed quote actually appear in the fetched document. Uses fuzzy matching for tolerance to formatting noise, but layers a separate, exact **numeric token gate** on top: every number, year, or percentage in the claim must literally appear in the matched text, because fuzzy similarity alone cannot distinguish a correct quote from one where the AI changed the one number that mattered.
6. **Verification tag** (`src/tag_schema.py`) — a record of *what was actually checked and what the result was*. A claim is never "verified" from one check alone — a perfect quote match paired with a spoofed source still correctly fails, because the tag bundles both checks together and the failing one overrides.
7. **Pipeline** (`src/pipeline.py`) — wires the checks together as separable, independently testable steps.
8. **Extraction loop** (`src/extraction.py`) — orchestrates all of the above for one claim, with retries that carry stage-specific feedback (a fetch failure gets different guidance than a wrong-quote failure) and stop early once an attempt stops making measurable progress, not after a fixed delay or count alone.

**This chain has actually been run live, end to end.** The first genuine attempt at a real claim returned `verified`, after three real, separately diagnosed bugs were found and fixed along the way: a missing search credential, a search provider whose entire free tier was removed by its vendor between when it was chosen and when it was tested, and a real server using a valid HTTP pattern (chunked transfer encoding) the original fetch design hadn't accounted for. Each was caught by reading the actual evidence in the structured log rather than guessing at the cause, and each is now locked in by a test that reproduces it. See `DESIGN_DECISIONS.md` for the full account of each.

Every check below the orchestration layer is deliberately generic: `domain_check`, `quote_match`, `page_fetch`, `url_compare`, and `web_search` know nothing about TSMC, climate frameworks, or what a "claim" means semantically. They take a URL/allowlist, a quote/document, a page to fetch, or a query string, and return a result. All company- and bucket-specific logic lives in the orchestration layer, so the same checks can eventually evaluate claims about any company, for any framework.

See `DESIGN_DECISIONS.md` for the full reasoning behind every module, including the approaches that were tried and rejected, and the real bugs found via adversarial review and live testing.

## Ground truth

Two company assessments have been independently rebuilt from primary sources to serve as evaluation baselines, deliberately chosen to stress-test different failure conditions:

- **TSMC** — abundant disclosure; tests whether verification can catch an error hiding inside real, extensive source material.
- **Frontier Lithium** — thin/pre-production disclosure; tests what an honest system says when there's almost nothing to verify, and the risk is marketing tone outrunning actual commitments rather than competing facts.

Further companies (Patagonia, TotalEnergies, Cheniere, Coal India, Vestas, Microsoft) are an identified backlog, each chosen to test a specific structural gap (e.g. a single entity with two opposed climate signatures; a same-product-opposite-use contradiction), not added yet, to avoid building extensive ground truth before the system that uses it exists.

## Status

The Bucket A pipeline is complete, hardened, and **live-verified end to end**: domain check, web search, URL-match enforcement, page fetch, quote match, tag schema, pipeline wiring, and the retry loop have all been run together against the real internet, not just tested in isolation with mocks.

Bucket B is now also complete and **live-verified end to end**, reaching the same milestone Bucket A reached, with its own real bug found and fixed along the way. The evidence structure (`CriterionEvidence`, `tag_schema.py`), the excerpt-finding module (`criterion_evidence.py`, with real NZIF 2.0 criteria text hand-transcribed and verified against the primary source after an earlier AI-reconstructed version was found to not match it), and the orchestrator (`bucket_b_pipeline.py`, which runs the full search → select → verify → fetch → extract chain independently for each NZIF criterion) are all built and tested. The first live run of the full orchestrated chain surfaced a real, genuine bug — invisible to any mocked test — in how search queries were being constructed; it was found, diagnosed, and fixed the same way every other live-only bug in this project has been: by reading real evidence rather than guessing, and by testing the fix against all six real criteria, not just the one that originally failed. Both Bucket A's and Bucket B's live tests now assert real, specific properties of the evidence found on a passing run, not just a bare status string, after the same "a passing test told us nothing useful" gap was found and fixed in both places.

Bucket B has since been extended with a second, independent evidence source: real TPI (Transition Pathway Initiative) Management Quality data (`tpi_extract.py`), built while adding Patagonia and TotalEnergies as real ground-truth companies. This started from a real architectural question, TPI's framework is genuinely different from NZIF's, with some of its per-company data rendered as a visual indicator grid initially assumed unreadable by a model, but verified, by inspecting the real raw HTML directly, to be deterministically parseable via a small, targeted HTML parser — confirmed against an independent source (the user's own RBC analysis) before being trusted. The module fetches both the current 23-indicator assessment and the full historical trend, finding and fixing two real bugs along the way (a wrong assumed JSON field name, and a page with multiple same-shaped dropdowns silently colliding), and represents Patagonia's genuine structural absence from TPI's universe (a real, confirmed 404, since Patagonia is privately held with no public market capitalisation) as a distinct, honest, named outcome rather than a generic failure. TPI evidence lives on its own `ClaimTag`, deliberately never sharing one with NZIF evidence, since the two are independent assessments that can and do disagree about the same company.

The NZIF side of both ground-truth companies has also been run live. TotalEnergies returned real, verified evidence for all six criteria on the first attempt, including a genuine, correctly-handled edge case: one evidence source came from a TotalEnergies-branded regional domain (`corporate.totalenergies.cn`) outside the deliberately narrow allowlist, and was correctly labeled `third_party` rather than silently treated as official. Patagonia returned only one verified criterion out of six — investigated rather than accepted at face value, the real cause was found to be Patagonia's own bot-detection system blocking the plain HTTP fetch entirely (the same class of problem encountered with TSMC's press release server, served by a different vendor here), not a quote-matching or prompting issue. The system's actual behavior under this real, unanticipated failure is the finding worth keeping: rather than fabricate plausible-sounding evidence when the real source was unreachable, it returned an honest `not_found_after_retries` for every blocked criterion — the project's founding thesis holding on a live case nobody had designed for. This is a real, currently open limitation, named explicitly rather than patched reflexively; see `DESIGN_DECISIONS.md` for the full diagnostic chain and why a fix is deliberately deferred to its own discussion rather than folded in here.

Bucket C is now complete and **live-verified end to end** — all three pieces built, tested, and committed. `bucket_triage.py` classifies a claim as Bucket A, Bucket C, or genuinely "ambiguous" before any search or fetch happens, with two tempting deterministic shortcuts (hedge-word detection, comparative-keyword detection) deliberately killed by real, constructed counterexamples. `source_extraction.py` gathers multiple independent sources and, for each one, independently proposes and verifies a claimed value and its stated definition, with a source included only if at least one of the two checks genuinely passed. `reconciliation.py` takes the list of verified `SourceFinding`s and groups the definition-bearing ones by shared underlying scope — using a single whole-list LLM call rather than pairwise comparisons (which would risk intransitive judgments), with one retry on malformed responses only and full defensive parsing on the result. Every source ends up in exactly one of five named slots: `groups` (two or more sources sharing a real-world scope), `distinct_sources` (confidently different), `unresolved` (relationship genuinely unclear), `no_definition_sources` (verified value, no stated scope), or `failed_reconciliation` (processing failure, not a judgment). Nothing is ever silently dropped. Live-verified on the real TSMC market-share claim: Sources A and B grouped, C distinct, D unresolved, in a single real API call.

All three buckets write to one shared structured log (`logs/evaluation_log.jsonl`). Every entry is tagged with `company_name` and `bucket`, so cross-bucket, cross-company history is queryable in one place. Bucket C's log entries record `outcome` (one of four named values: `reconciled`, `no_definitions`, `sole_source`, `failed`), `attempts_made`, and counts for each of the five output slots — enough to diagnose any real live failure without writing a throwaway diagnostic script. `company_name` is a required, explicitly-supplied parameter to `reconcile_sources`, the same "never inferred" rule already enforced on `extract_claim_evidence` and `run_bucket_b_pipeline`.

The deliberate design split this bucket required holds throughout: a "what did the company claim" fact-finding half, verified the same way Bucket A is (via `quote_match`, never trusted on a model's word alone), kept separate from the "does this meet the framework's bar" judgment half, which the system never attempts to automate — a human reads the real criterion wording alongside the verified evidence and decides. See `DESIGN_DECISIONS.md` for the full account of both this bucket's design history and the live bugs found while proving it actually works.

Bucket D has no implementation yet — only the `AssumptionsStatedEvidence` placeholder in `tag_schema.py`. A Bucket C orchestrator (wiring `bucket_triage` → `gather_source_findings` → `reconcile_sources` into one callable, the way `bucket_b_pipeline.py` wires Bucket B) is also not yet built. Both are the natural next pieces.

Breadth (more ground-truth companies) is an explicit, deliberate backlog, not a current priority.

## Stack

Python for verification logic (string matching, deterministic checks — the right tool for this specific job) and the external calls in `extraction.py` (OpenAI for the LLM, Tavily for search). TypeScript may be introduced later for a dashboard/UI layer if one is built, on the same principle: use each tool where it actually fits, rather than picking one language and forcing everything through it.

## Development

```
pip install -r requirements.txt
cp .env.example .env                 # add your own OPENAI_API_KEY and TAVILY_API_KEY
python -m pytest tests/ -v           # run all tests (live-API tests are skipped by default)
RUN_LIVE_API=1 python -m pytest tests/test_extraction.py -v -m live_api  # run the real, full live chain deliberately
python -m black --check src/ tests/  # check formatting (line length 88)
python -m flake8 src/ tests/         # lint (config in .flake8)
```

black owns code line length; flake8 is configured to ignore E501/E203/W503 (the standard combination when using both together), since black does not rewrap comments or string literals.