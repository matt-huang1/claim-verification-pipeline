# ADR-0004: tag_schema.py — what "verified" means and who may say it

## Status

Accepted

## Context

`tag_schema.py` defines `ClaimTag` — one record per claim, bundling the results of every check that ran against it, with `overall_status` computed from the attached evidence rather than settable directly. The design question is how to represent verification state so that a confident label cannot be earned without the underlying checks actually passing.

## Decision

- **One tag per claim, not one tag per check.** If `domain_check` and `quote_match` produced separate, free-floating tags, a reviewer could read the quote-match tag alone, see `unique, 100.0`, and stop — never realizing domain legitimacy was never checked, or had failed.
- **Typed evidence dataclasses, not a generic dict.** A generic dict could be built with the wrong evidence shape in the wrong slot (a domain-check-shaped dict stored where quote-match evidence belongs) and nothing would catch it until something tried to read a missing field. Explicit dataclasses make this impossible at construction time — enforced by the type system, not convention.
- **`overall_status` is a computed `@property`, not a plain field.** If it were settable, it could be set to `"verified"` by mistake, by a future bug, or by someone taking a shortcut under time pressure, without the underlying checks having actually passed. Making it read-only and recomputed every access makes that specific failure mode structurally impossible.
- **Bucket-specific terminal success states, not a shared "verified".** Bucket C's terminal success state is `"disambiguated"`; Bucket D's is `"assumptions_explicit"`. Bucket C has no single authoritative source to check against by definition — that is the entire reason it is Bucket C and not Bucket A. Reusing "verified" would flatten exactly the distinction Bucket D's label exists to preserve.

## Consequences

- **One-tag-per-claim proven concretely:** built a tag with a failing domain check (`tsmc.com.evil.com`) paired with a textually perfect quote match (`status=unique, score=100.0`). Correctly returns `overall_status="source_illegitimate"`, not `"verified"`. A one-tag-per-check design would have gotten this exact case wrong.
- **`overall_status` immutability tested directly:** `tag.overall_status = "verified"` raises `AttributeError`.
- **The Bucket C labeling bug, found by a reviewer reading the code closely (not running it):** the first version returned `"verified"` for Bucket C once source definitions were reconciled. This was wrong, and inconsistent with how Bucket D was already handled in the same function, for the identical structural reason. Fixed to `"disambiguated"`. This is the same failure mode the whole project exists to catch (a confident label not actually earned by what it claims), found this time by a reviewer reading carefully rather than by adversarial execution — both modes of review caught real things, for different bug classes.
