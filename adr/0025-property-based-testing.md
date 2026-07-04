# ADR-0025: Property-based tests for the deterministic checks

## Status

Accepted

## Context

The example-based suites lock in specific, historically-found
counterexamples (the port-injection bypass, the hallucinated year, the
self-collision window bug). What they cannot say is that the invariants hold
for inputs nobody thought to write down. The deterministic checks
(`quote_match`, `domain_check`, `url_compare`) are pure functions over
strings - exactly the shape property-based testing is built for.

## Decision

Add a Hypothesis suite (`tests/test_properties.py`) asserting the invariants
that must hold for EVERY input, not just the curated examples:

- **Traceability:** every quote-match candidate's text equals
  `document[start_index:end_index]` - the guarantee that lets a human audit a
  tag back to an exact source location.
- **Numeric-gate soundness:** a `"unique"` verdict is only ever issued when
  every numeric token in the claimed quote appears in the matched span; and a
  constructed quote whose year appears nowhere in the document never verifies
  while its honest counterpart always does.
- **Domain-check soundness:** true subdomains always pass; prefix-spoof hosts
  (`entry.attacker.tld`) never pass; and - targeting the netloc bug class
  from ADR-0002 metamorphically - injected credentials and ports never change
  the decision or the extracted domain.
- **URL-compare boundaries:** cosmetic variants always compare equal; a path
  differing by any appended characters never does.

The suite is pinned deterministic: a registered profile sets
`derandomize=True` and `deadline=None`, so CI never flakes on seed variance
or generation timing. The properties run in the ordinary deterministic suite
(no network, no LLM) and count toward the coverage gate.

## Consequences

- The suite passed on first run against the current implementations - no new
  bug found. That is itself information: the example-based fixes generalise.
- `hypothesis` is a dev-extra dependency only; the runtime package is
  unchanged.
- The properties document the invariants more precisely than prose: a future
  change that weakens the numeric gate or reintroduces netloc-style parsing
  fails hundreds of generated cases, not one hand-picked example.
