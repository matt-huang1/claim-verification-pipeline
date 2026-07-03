# ADR-0001: Founding principle — non-discriminating verification

## Status

Accepted

## Context

A TSMC climate transition assessment was produced using AI-assisted research. It was "verified" by re-asking the same model "are you sure" multiple times in one session. That is not independent verification — the second and third pass have no new information and no different reasoning process from the first. The actual error that slipped through: an IIGCC NZIF 2.0 bucket label, "climate solutions bucket," that does not exist in the framework's five real alignment tiers. It went out in a real document before being caught — caught only by going back to the actual primary source document directly.

## Decision

Adopt as the core thesis behind every decision in this project: **a check only counts if it would have given a different answer in the world where the claim was false.** A check that says "looks fine" regardless of truth is not a check, it is theater. This is called **non-discriminating verification** throughout, and it is the thing every subsequent fix corrects for.

Re-asking a model is not verification. Going to the primary source is.

## Consequences

- This is the founding case for every ADR that follows.
- The design record captures *why* each decision was made, including approaches tried and rejected, so every decision can be defended unprompted. The resulting structure is described in [ARCHITECTURE.md](../ARCHITECTURE.md); current gaps in [KNOWN_LIMITATIONS.md](../KNOWN_LIMITATIONS.md).
- The system never decides whether a claim is "good enough"; that judgment belongs to a human. What it does is gather, verify, and structure the evidence so the human decision is grounded in primary sources, not in what the model thinks it remembers.
