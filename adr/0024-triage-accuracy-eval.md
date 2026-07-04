# ADR-0024: Scoring the triage router against the labeled ground truth

## Status

Accepted

## Context

The adversarial suite (`adversarial_eval.py`) turns "the deterministic
verifier discriminates" into a CI-enforced number. The one LLM judgment
sitting in front of that verifier - `triage_claim`, which routes a claim to
Bucket A, C, or D - had only two live test cases behind it. The ground truth
(`ground_truth.py`) already files every labeled claim under a bucket, so
routing accuracy is directly measurable.

## Decision

- **A scored spot-check, not a benchmark.** `agent_eval/triage_eval.py`
  builds one case per labeled ground-truth claim (14 at time of writing),
  routes each through the real `triage_claim`, and scores against the bucket
  the claim is filed under. `scripts/triage_eval.py` prints the per-claim
  table with the model's reasoning on every miss.
- **Run deliberately, never in CI.** It costs one real API call per claim and
  is non-deterministic. A CI gate on a 14-claim LLM measurement would fail
  builds on model drift unrelated to any code change - the opposite of what a
  gate is for. The deterministic harness (case construction, scoring, summary
  math) IS tested in the normal suite (`tests/test_triage_eval.py`).
- **Record misses instead of tuning them away.** Rewriting the triage prompt
  to pass the exact claims the eval measures would be teaching to the test -
  the project's own founding mistake, applied to its own scorecard. Prompt
  changes should come from new labeled data, with this eval as the
  before/after measurement.

## Consequences

- **First scored run (2026-07-04): 12/14 routed correctly (86%)** -
  bucket_a 7/8, bucket_c 2/2, bucket_d 3/4. Both misses are genuine taxonomy
  boundary findings, not model noise:
  - **Systemic-causal claims:** "Microsoft's AI data centre expansion is
    making it harder for everything else to decarbonise" is filed as Bucket D
    (counterfactual about systemic effect) but routed to bucket_c, with
    coherent reasoning about contested attribution methodology. The claim is
    present-tense causal rather than explicitly counterfactual - the prompt's
    Bucket D definition ("future-facing or counterfactual") genuinely does
    not cover it cleanly.
  - **Absence claims:** "Frontier Lithium has no stated net zero or interim
    climate target" is filed as Bucket A (a confirmed absence, checked
    against the company's disclosures) but routed to bucket_c. No single
    document states an absence, so "does a single authoritative source exist"
    reads differently for negative claims - a real gap in the taxonomy's
    distinguishing test, previously invisible.
- Both findings are exactly what the spot-check exists to surface: the worked
  counterexamples in the triage prompt cover misleading wording, but neither
  systemic-causal nor absence claims. Revisit the prompt when there are
  enough labeled claims of these two shapes to test a change against claims
  the eval does not score.
