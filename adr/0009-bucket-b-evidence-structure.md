# ADR-0009: Bucket B verification - the evidence structure

## Status

Accepted (evidence structure built and tested; extraction logic designed, not yet implemented)

## Context

Bucket B covers a company's alignment with a specific external framework (NZIF). The first version of `ReasoningCheckEvidence` (built weeks earlier, before any real Bucket B work) was a pair of booleans - `reasoning_shown` and `framework_classification_checked`. The instinct was that Bucket A's verification is a clean pass/fail, so Bucket B's should look similar. It does not survive contact with a real case.

Working through TSMC's real NZIF alignment classification end to end surfaced the actual shape: "Does TSMC have a decarbonisation plan" is not one question - it splits into "what did TSMC actually claim its plan is" (a fact, with a real source, close to Bucket A's territory) and "does what they claimed meet NZIF's specific bar for what counts as a decarbonisation plan" (a judgment applying an external framework's criteria to a fact, not itself a fact). The boolean placeholder collapsed the two together.

## Decision

- **Reject boolean placeholders and keyword/pattern adequacy checks.** A second, even more tempting shortcut - keyword/pattern detection ("does the plan have years, action verbs, references to RE60/RE100") to mechanically decide adequacy - was tested by constructing a case it would wrongly pass: a vague plan with years and action verbs but no real quantified substance would clear a keyword check while failing NZIF's actual bar. The underlying judgment cannot be reduced to any checkable pattern; it requires a human reading the criterion and the evidence side by side, which is exactly what the bucket was defined to mean.
- **`CriterionEvidence` structure:** per NZIF criterion (ambition, targets, disclosure, decarbonisation plan, etc.), records `criterion_name`, the real `criterion_text` pulled from the actual NZIF document (not paraphrased or remembered), `evidence_text` the AI found, `evidence_source_url`, and `evidence_source_type` ("official" vs "third_party" - explicitly distinguished, never silently treated as equivalent). Deliberately contains **no verdict field of any kind**. `ClaimTag.criteria_evidence` is a list, since a single Bucket B claim is typically checked against several NZIF criteria at once.
- **`overall_status` for Bucket B:** with no automated verdict possible, the success state is `"criteria_evidence_gathered"` (parallel to Bucket C's `"disambiguated"` and Bucket D's `"assumptions_explicit"` - none claim "verified" in the Bucket A sense). Incompleteness check is `if not self.criteria_evidence`.

## Consequences

- **Empty-list incompleteness tested directly:** does the check treat an empty list the same as `None`, or would an empty-but-present list slip through as if real evidence existed? Python's `not []` and `not None` both evaluate `True`, so this single condition already covers both cases without the extra explicit length check that might otherwise have been assumed necessary.
- **A deliberately deferred gap, found by testing the design against TSMC's own report:** TSMC's Climate and Nature Report presents some criterion-relevant evidence (e.g. its emissions trajectory) as a chart, not only prose. Checked directly whether this breaks the text-only structure: it does not, for cases encountered so far, because TSMC's chart has a real textual description of the same trend elsewhere in the same report - no case yet where the only evidence for a criterion exists exclusively as an image. Same class of gap as `page_fetch.py`'s deferred table/image extraction; named explicitly, revisited only if a real company's evidence turns out to be genuinely visual-only.
- **Status:** the evidence structure is built and tested in `tag_schema.py`. The actual extraction logic (finding NZIF's real criterion wording, finding a company's real corresponding claim, sourcing both correctly) is designed in principle but not yet implemented as a working module.
