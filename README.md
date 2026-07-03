# Agent Evaluation Framework

[![CI](https://github.com/matt-huang1/agent-evaluation-framework/actions/workflows/ci.yml/badge.svg)](https://github.com/matt-huang1/agent-evaluation-framework/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue.svg)](pyproject.toml)

A verification layer for AI-assisted climate transition research at an asset manager. Its job is to catch errors before they reach client documents — specifically, cases where a model produces plausible-sounding claims not grounded in primary sources.

> **Independent project.** This is a personal portfolio project, built and maintained solely by me. It is not affiliated with, endorsed by, or produced on behalf of any employer or client. "An asset manager" refers generically to the professional context that motivated the work; no client is named, and the repository contains no confidential, proprietary, or otherwise non-public information. Every claim and data point is verified against publicly available primary sources — company disclosures and the public [NZIF](https://www.iigcc.org/hubfs/2023%20resources%20updates/NZIF%202.0.pdf) and [TPI](https://www.transitionpathwayinitiative.org/) frameworks.

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

## Status

All four verification types are implemented, tested, and live-verified end to end — 316 passing deterministic tests plus per-module live API tests. See [CURRENT_STATUS.md](CURRENT_STATUS.md) for the full module inventory and live-verified milestones.

## Does the verifier actually work?

The project's thesis is that a check only counts if it would give a different
answer in the world where the claim is false. That is a testable claim, so it
is tested directly. A deterministic self-evaluation feeds the Bucket A verifier
adversarial proposals — spoofed domains (including prefix and port-injection
look-alikes), quotes with a hallucinated year or figure, and quotes fabricated
outright — alongside honest controls, and asserts each is caught with the
correct, specific status:

```
python scripts/adversarial_eval.py     # offline, no API key, no cost
```

**Current result: 7/7 adversarial cases caught (100%), 2/2 clean controls
verified.** The suite is offline and runs in CI on every push
([tests/test_adversarial_eval.py](tests/test_adversarial_eval.py)), so a change
that silently weakened a check turns the build red.

## Running the results browser

![The results browser showing pre-computed verification outcomes](docs/results-browser.png)

```
python scripts/run_batch.py      # run pipeline on curated claims, writes data/results.json
python -m http.server 8080       # serve from repo root
# open http://localhost:8080
```

The browser reads `data/results.json` if present, and otherwise falls back to the
bundled `data/sample_results.json` — a real, committed pipeline run against live
sources (the same eight claims across all four buckets) — so a fresh clone shows
genuine verified results immediately. This is the state shown above. Every entry
is drawn from publicly available primary sources; see the independence note at the
top of this README.

## Development

```
pip install -e ".[dev]"                     # package + test/lint/type-check tooling
cp .env.example .env                        # add OPENAI_API_KEY and TAVILY_API_KEY
python -m pytest -m "not live_api" -q      # full deterministic suite
python scripts/adversarial_eval.py                 # verifier self-evaluation (offline)
RUN_LIVE_API=1 python -m pytest -m live_api -v   # live tests (cost real API calls)
python -m black --check .                          # formatting check
python -m flake8 .                                 # lint
python -m mypy                                     # static type check
```

The runtime package (`pip install -e .`) pulls in only what the pipeline needs to
run; pytest, black, flake8, and mypy live in the `dev` extra so they are not forced
on consumers of the package. CI runs the full suite on Python 3.10–3.12.

## Documentation

| Document | What it covers |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | How the system is structured: claim routing, module map, layering principles, status vocabulary |
| [CURRENT_STATUS.md](CURRENT_STATUS.md) | What is built, tested, and live-verified today |
| [ROADMAP.md](ROADMAP.md) | What's not yet built, and work deliberately deferred with stated triggers to revisit |
| [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) | Real, current gaps — named explicitly rather than left as implicit TODOs |
| [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md) | Index of Architecture Decision Records (ADRs) |
| [adr/](adr/) | Individual Architecture Decision Records, one per decision |

See [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md) for the ADR index and the full design trail, including every rejected alternative and every real bug found in live runs. Each decision is recorded as an individual ADR under [adr/](adr/).
