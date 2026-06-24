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

## What's built: a complete Bucket A vertical slice, AI-to-verified-tag

The repo now contains a fully-specified, fully-tested, end-to-end pipeline for Bucket A claims, from a plain-English claim to a verified record, with no untrusted step in between:

1. **Domain legitimacy check** (`src/domain_check.py`) — does the claimed source URL actually belong to the company, checked against an allowlist, not a third-party cross-reference (a self-announcement doesn't need corroboration; it needs confirmation it came from the right place).
2. **Quote match check** (`src/quote_match.py`) — does the claimed quote actually appear in the source document. Uses fuzzy matching for tolerance to formatting noise, but layers a separate, exact **numeric token gate** on top: every number, year, or percentage in the claim must literally appear in the matched text, because fuzzy similarity alone cannot distinguish a correct quote from one where the AI changed the one number that mattered.
3. **Verification tag** (`src/tag_schema.py`) — a record of *what was actually checked and what the result was*. A claim is never "verified" from one check alone — a perfect quote match paired with a spoofed source still correctly fails, because the tag bundles both checks together and the failing one overrides.
4. **Pipeline** (`src/pipeline.py`) — wires the checks together: run the checks, build the tag from the results, or do both in one call. Built as separable steps specifically so the wiring logic can be tested with fake inputs, without needing real checks to run every time.
5. **Extraction layer** (`src/extraction.py`) — the only module that calls a real LLM (OpenAI). Proposes a candidate source URL and quote for a claim, runs it through the deterministic pipeline above, and retries with specific feedback if it fails — stopping early once retries stop making measurable progress, not after a fixed delay. A cheap, deliberately incomplete pre-check rejects claims with no checkable content (no number, no exclusivity/ranking word) before any paid call is made. Every attempt, successful or not, is logged to `logs/extraction.jsonl` so failures can be audited by a human rather than trusted on the system's own say-so.

Every check below the extraction layer is deliberately generic: `domain_check` and `quote_match` know nothing about TSMC, climate frameworks, or what a "claim" means semantically. They take a URL/allowlist or a quote/document and return a result. All company- and bucket-specific logic lives in the orchestration layer, not in the checks themselves, so the same checks can eventually evaluate claims about any company, for any framework.

See `DESIGN_DECISIONS.md` for the full reasoning behind each module, including the approaches that were tried and rejected, and the real bugs found via adversarial review (a working domain-spoofing exploit, a hallucinated-number gate, and others).

## Ground truth

Two company assessments have been independently rebuilt from primary sources to serve as evaluation baselines, deliberately chosen to stress-test different failure conditions:

- **TSMC** — abundant disclosure; tests whether verification can catch an error hiding inside real, extensive source material.
- **Frontier Lithium** — thin/pre-production disclosure; tests what an honest system says when there's almost nothing to verify, and the risk is marketing tone outrunning actual commitments rather than competing facts.

Further companies (Patagonia, TotalEnergies, Cheniere, Coal India, Vestas, Microsoft) are an identified backlog, each chosen to test a specific structural gap (e.g. a single entity with two opposed climate signatures; a same-product-opposite-use contradiction), not added yet, to avoid building extensive ground truth before the system that uses it exists.

## Status

The Bucket A vertical slice is complete and hardened: domain check, quote match, tag schema, pipeline wiring, and the AI extraction layer all built, tested, and verified against real adversarial cases (including a confirmed domain-spoofing exploit and a confirmed hallucinated-number bypass, both fixed). Buckets B, C, and D are designed in principle (see the table above and `tag_schema.py`'s evidence types) but not yet implemented — that's the natural next piece of depth. Breadth (more ground-truth companies) is an explicit, deliberate backlog, not a current priority.

## Stack

Python for verification logic (string matching, deterministic checks — the right tool for this specific job) and the one LLM call in `extraction.py` (OpenAI API). TypeScript may be introduced later for a dashboard/UI layer if one is built, on the same principle: use each tool where it actually fits, rather than picking one language and forcing everything through it.

## Development

```
pip install -r requirements.txt
cp .env.example .env                 # add your own OPENAI_API_KEY
python -m pytest tests/ -v           # run all tests (the one live-API test is skipped by default)
RUN_LIVE_API=1 python -m pytest tests/test_extraction.py -v -m live_api  # run the real API test deliberately
python -m black --check src/ tests/  # check formatting (line length 88)
python -m flake8 src/ tests/         # lint (config in .flake8)
```

black owns code line length; flake8 is configured to ignore E501/E203/W503 (the standard combination when using both together), since black does not rewrap comments or string literals.
