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

## Status

All four verification types are implemented, tested, and live-verified end to end — 311 passing deterministic tests plus per-module live API tests. See [CURRENT_STATUS.md](CURRENT_STATUS.md) for the full module inventory and live-verified milestones.

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
python -m black --check agent_eval/ tests/        # formatting check
python -m flake8 agent_eval/ tests/               # lint
```

## Documentation

| Document | What it covers |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | How the system is structured: claim routing, module map, layering principles, status vocabulary |
| [CURRENT_STATUS.md](CURRENT_STATUS.md) | What is built, tested, and live-verified today |
| [ROADMAP.md](ROADMAP.md) | What's not yet built, and work deliberately deferred with stated triggers to revisit |
| [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) | Real, current gaps — named explicitly rather than left as implicit TODOs |
| [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md) | The full design decision record |

See [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md) for the full design trail, including every rejected alternative and every real bug found in live runs.
