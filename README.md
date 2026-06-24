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

## What's built so far

This repo currently contains one fully-specified, fully-tested **vertical slice**: a single Bucket A claim (TSMC's 2023 commitment to accelerate its renewable energy target to 2040) traced end-to-end through:

1. **Domain legitimacy check** (`src/domain_check.py`) — does the claimed source URL actually belong to the company, checked against an allowlist, not a third-party cross-reference (a self-announcement doesn't need corroboration; it needs confirmation it came from the right place).
2. **Quote match check** (`src/quote_match.py`) — does the AI-extracted quote actually appear in the source document, found via fuzzy string matching, returning the top 3 candidate matches with similarity scores rather than a single silent pick.
3. **Verification tag** (`src/tag_schema.py`) — a record of *what was actually checked and what the result was*, not just which bucket a claim belongs to. A tag that only says "Bucket A, deterministic" without recording the actual pass/fail result and match score would itself be a non-discriminating check wearing a more official-looking label.

Both checks are deliberately generic: they know nothing about TSMC, climate frameworks, or what a "claim" means semantically. They take a URL/allowlist or a quote/document, and return a result. All company- and bucket-specific logic lives in the orchestration layer (`src/pipeline.py`), not in the checks themselves, so the same checks can eventually evaluate claims about any company, for any framework.

## Ground truth

Two company assessments have been independently rebuilt from primary sources to serve as evaluation baselines, deliberately chosen to stress-test different failure conditions:

- **TSMC** — abundant disclosure; tests whether verification can catch an error hiding inside real, extensive source material.
- **Frontier Lithium** — thin/pre-production disclosure; tests what an honest system says when there's almost nothing to verify, and the risk is marketing tone outrunning actual commitments rather than competing facts.

Further companies (Patagonia, TotalEnergies, Cheniere, Coal India, Vestas, Microsoft) are an identified backlog, each chosen to test a specific structural gap (e.g. a single entity with two opposed climate signatures; a same-product-opposite-use contradiction), not added yet, to avoid building extensive ground truth before the system that uses it exists.

## Development

```bash
python -m pytest tests/ -v                          # run the test suite
python -m black --check --line-length=88 src/ tests/  # formatting check
python -m flake8 src/ tests/                         # lint
```

Formatting is owned by **black** (line length 88, configured in `pyproject.toml`). Linting is **flake8**, configured in `.flake8` (stock flake8 does not read `pyproject.toml`, so the config lives in its own file rather than being silently ignored).

`.flake8` sets `extend-ignore = E501, E203, W503` — the standard combination when running black and flake8 together. black already enforces line length on everything it can safely reformat, but it deliberately does not rewrap comments or string literals; E501 would otherwise flag design-reasoning comments and test-fixture strings that black cannot fix and that are not worth mangling by hand. E203 and W503 are ignored because black's formatting intentionally conflicts with them.

Test files import from `src/` via `tests/conftest.py`, which puts `src/` on the path once for all tests — so individual test files import their module directly at the top, with no per-file path manipulation.

## Status

Early. One vertical slice built and tested. Full pipeline (RAG retrieval, Bucket B/C/D handling, the human-judgment layer, the facts-and-figures dashboard) not yet built.

## Stack

Python for verification logic (string matching, deterministic checks — the right tool for this specific job). TypeScript may be introduced later for a dashboard/UI layer if one is built, on the same principle: use each tool where it actually fits, rather than picking one language and forcing everything through it.
