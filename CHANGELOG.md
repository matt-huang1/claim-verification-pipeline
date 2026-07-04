# Changelog

All notable changes to this project. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[semantic versioning](https://semver.org/) (0.x: minor bumps may change
behaviour). The reasoning behind each change lives in the
[ADRs](DESIGN_DECISIONS.md); this file records *what* shipped and *when*.

## [0.3.0] - 2026-07-04

Hardening release driven by an external principal-level review.

### Changed
- **Search unavailability is a named failure.** `search_for_source` now
  raises `SearchUnavailable` when the search layer cannot run at all
  (missing `TAVILY_API_KEY`, auth/quota/network failure) instead of
  returning an empty list. The state surfaces as `search_unavailable` in the
  extraction status, the structured log, and the dispatcher outcome for
  Buckets A, B, and C - a configuration error can no longer masquerade as an
  honest "unverifiable"/"incomplete" verdict ([ADR-0026](adr/0026-search-unavailability.md)).
- **Triage runs exactly once per dispatched claim.** Bucket C claims routed
  by the dispatcher's own triage were previously re-triaged inside the C
  pipeline - a duplicate LLM call whose nondeterminism could contradict the
  routing already made ([ADR-0027](adr/0027-dispatcher-triages-once.md)).
- CI installs against pinned dependency versions (`constraints.txt`) on
  Python 3.10-3.13, and a `pip-audit` job fails the build on known
  vulnerabilities. The first audit run caught and fixed a vulnerable
  `requests` pin (CVE-2024-47081, CVE-2026-25645).
- `scripts/` are runnable from a fresh clone without the editable install
  (repo root added to `sys.path`; dependencies still required).
- Packaging metadata completed (`readme`, `license`, `authors`,
  `classifiers`, `project.urls`); the version is single-sourced from
  `agent_eval.__version__`.

### Fixed
- `ARCHITECTURE.md` and `ROADMAP.md` had gone stale on triage routing: both
  still said triage never routes to Bucket D, contradicting the code and
  [ADR-0024](adr/0024-triage-accuracy-eval.md). The diagram and routing
  rules now match the implementation.

### Added
- Dependabot (pip + GitHub Actions, weekly), this changelog, and regression
  tests for both behaviour changes.

## [0.2.0] - 2026-07-04

- Renamed to `claim-verification-pipeline`.
- Verification runs against the post-redirect URL, so content is checked
  against where it actually came from ([ADR-0023](adr/0023-redirect-revalidation.md)).
- Scored triage spot-check against the labeled ground truth
  (first run 12/14; both misses recorded as taxonomy findings,
  [ADR-0024](adr/0024-triage-accuracy-eval.md)).
- Hypothesis property suite asserting the deterministic checks' invariants
  for every generated input ([ADR-0025](adr/0025-property-based-testing.md)).

## [0.1.0] - 2026-06-22

- Initial release: the four-bucket claim taxonomy, deterministic Bucket A
  verification (domain check + quote match with numeric token gate),
  evidence-gathering pipelines for Buckets B/C/D, the adversarial
  self-evaluation, the results browser, and ADRs 0001-0022.
