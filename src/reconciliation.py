"""
reconciliation.py

Bucket C reconciliation: given a list of SourceFindings already gathered by
source_extraction.py, group the definition-bearing sources by whether they
share the same underlying real-world scope, and return a SourcePluralityEvidence
record that accounts for every input source exactly once.

THREE STEPS:

  1. Deterministic split (no LLM): route sources with definition_found=False
     directly to no_definition_sources. If 0 or 1 definition-bearing sources
     remain, return immediately without any LLM call.

  2. LLM call: pass all definition-bearing sources to the model in one batch.
     The model places each source_url into groups (2+ sharing a scope),
     distinct (confidently different), or unresolved (unclear).

  3. Validation + retry: a response is malformed if any source_url is missing,
     hallucinated, duplicated, or if any group has fewer than 2 members, or if
     any entry is missing a non-empty reasoning field. On malformed attempt 1,
     retry once with specific feedback. If attempt 2 also fails, every
     definition-bearing source goes into failed_reconciliation.

WHY ONE BATCH CALL, NOT ONE-PAIR-AT-A-TIME:

  Comparing sources pairwise (A vs B, A vs C, B vs C, ...) would require O(n²)
  LLM calls and would force the model to make n(n-1)/2 independent decisions
  that may not be mutually consistent (A grouped with B, B grouped with C, but
  A distinct from C — a logical contradiction the batch call cannot produce).
  A single batch call over all sources is both cheaper and structurally more
  coherent: the model sees all definitions simultaneously and must produce a
  globally consistent assignment.

WHY failed_reconciliation IS DISTINCT FROM unresolved:

  "Unresolved" is a genuine judgment outcome: the model read the definition
  and concluded "I cannot confidently place this." That is a real, informative
  finding about the source's definition quality. "failed_reconciliation" means
  the model never produced a usable response at all after retries — a
  processing failure, not a judgment. Collapsing them would make it impossible
  for a human reviewer to distinguish "this source's definition was genuinely
  ambiguous" from "the model broke trying to read it."

WHY THE WHOLE RESPONSE IS INVALIDATED ON ANY DEFECT:

  Partially trusting a malformed response (e.g. keeping the groups that parsed
  correctly while discarding entries with missing reasoning) would produce
  silently incomplete output — some sources would be neither in the good part
  of the response nor in failed_reconciliation, effectively dropped. The only
  honest handling of a malformed response is to treat the whole batch as
  unprocessed and retry, then if still failing, move everything to
  failed_reconciliation. Nothing is ever silently dropped.
"""

import json
import os

from dotenv import load_dotenv

from tag_schema import (
    DefinitionGroup,
    DistinctFinding,
    SourceFinding,
    SourcePluralityEvidence,
    UnresolvedFinding,
)

load_dotenv()

MODEL = os.getenv("OPENAI_MODEL", "gpt-5-nano")

# Hard cap on LLM attempts for one reconciliation call. The second attempt
# includes specific feedback about the first attempt's defect; a third would
# add nothing not already communicated.
_MAX_RECONCILIATION_ATTEMPTS = 2

# Hardcoded reasoning for the deterministic single-source path.
_SOLE_SOURCE_REASONING = (
    "Only source with a stated definition — no peer to compare against."
)

# Feedback constants for specific malformed-response defects.
# Named constants so tests can import and assert the exact strings.
_MALFORMED_JSON_FEEDBACK = (
    "your previous response could not be parsed as JSON - respond "
    "with ONLY a valid JSON object with exactly three fields: "
    "'groups', 'distinct', 'unresolved'"
)
_HALLUCINATED_URL_FEEDBACK = (
    "your previous response included a source_url not present in "
    "the provided sources list - only use source_urls from the "
    "input you were given"
)
_MISSING_URL_FEEDBACK = (
    "your previous response did not account for every source_url - "
    "every input source_url must appear exactly once across groups, "
    "distinct, and unresolved"
)
_DUPLICATE_URL_FEEDBACK = (
    "your previous response placed the same source_url in more than "
    "one output list - each source_url must appear exactly once"
)
_SINGLE_MEMBER_GROUP_FEEDBACK = (
    "your previous response contained a group with only one member - "
    "a group requires at least two source_urls; use 'distinct' for "
    "a source that stands alone"
)
_MISSING_REASONING_FEEDBACK = (
    "your previous response was missing a 'reasoning' field on one "
    "or more entries - every group, distinct entry, and unresolved "
    "entry must include a non-empty 'reasoning' string"
)

_SYSTEM_PROMPT = """\
You reconcile source definitions for a factual claim. Given a claim and a list \
of sources, each with a stated definition of the category the claim is about, \
you place every source into exactly one of three categories:

  groups    — Two or more sources that share the same underlying real-world \
scope, regardless of how differently they word their definition. What matters \
is whether they are describing the same actual set of entities, not whether \
they use the same words.

  distinct  — A source whose scope is clearly different from every other source \
(and so cannot be grouped with any of them). Use this for a source that \
unambiguously defines a different real-world boundary.

  unresolved — A source whose definition is present but too vague to confidently \
place in any group or call distinct. Note: "unresolved" is not the same as \
having no definition at all — you will only receive sources that have a stated \
definition. Use "unresolved" when the definition is too vague to judge.

THE REAL DISTINGUISHING TEST — whether two definitions describe the same actual \
real-world scope, not whether they use the same words. Here are worked examples:

  Source A: "pure-play foundry market, excluding IDMs' in-house fabrication \
(Samsung, Intel)"
  Source B: "merchant foundry market, excluding integrated device manufacturers' \
captive capacity"
  → GROUPED. Differently worded, same real scope — both explicitly exclude IDM \
in-house/captive fab capacity.

  Source C: "total semiconductor manufacturing capacity, including in-house IDM \
fabrication"
  → DISTINCT. Clearly different, explicitly broader scope — includes what A and \
B explicitly exclude.

  Source D: "the foundry market" (no further qualification)
  → UNRESOLVED. States something but not enough to confirm or rule out \
membership in any group. Not the same as having no definition at all — D did \
state a scope, just too vaguely to place confidently.

OUTPUT FORMAT:

Respond with ONLY a JSON object with exactly three fields:

  "groups": a list of objects, each with:
    - "member_source_urls": list of 2+ source_urls sharing the same scope
    - "shared_definition_label": a short human-readable label for the scope
    - "reasoning": a non-empty string explaining why these sources share a scope

  "distinct": a list of objects, each with:
    - "source_url": the source_url
    - "reasoning": a non-empty string explaining why this source is distinct

  "unresolved": a list of objects, each with:
    - "source_url": the source_url
    - "reasoning": a non-empty string explaining what made this source unclear

Every source_url from the input must appear exactly once across all three output \
lists combined. Do not invent source_urls that were not in the input.
"""


def _default_llm_call(
    claim_text: str,
    findings: list[dict],
    feedback: str | None,
) -> dict:
    """Real LLM call. Tests inject a fake via llm_fn."""
    from openai import OpenAI

    client = OpenAI()

    sources_text = "\n\n".join(
        f"Source URL: {f['source_url']}\n"
        f"Claimed value: {f['claimed_value'] or 'not stated'}\n"
        f"Definition: {f['definition_text']}"
        for f in findings
    )

    user = f"Claim: {claim_text}\n\nSources:\n{sources_text}"
    if feedback:
        user += f"\n\nThe previous response was invalid because {feedback}. Please try again."

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


def _validate_response(
    raw: dict,
    expected_urls: set[str],
) -> tuple[bool, str | None]:
    """
    Check a parsed LLM response for all structural defects.

    Returns (is_valid, feedback_constant_or_None).
    Checks are ordered so the most actionable feedback is returned first.
    """
    groups = raw.get("groups", [])
    distinct = raw.get("distinct", [])
    unresolved = raw.get("unresolved", [])

    # Collect all source_urls from the response
    seen: list[str] = []
    for g in groups:
        seen.extend(g.get("member_source_urls", []))
    for d in distinct:
        url = d.get("source_url", "")
        if url:
            seen.append(url)
    for u in unresolved:
        url = u.get("source_url", "")
        if url:
            seen.append(url)

    seen_set = set(seen)

    # Hallucinated URL
    if not seen_set.issubset(expected_urls):
        return False, _HALLUCINATED_URL_FEEDBACK

    # Duplicate URL
    if len(seen) != len(seen_set):
        return False, _DUPLICATE_URL_FEEDBACK

    # Missing URL
    if seen_set != expected_urls:
        return False, _MISSING_URL_FEEDBACK

    # Group with < 2 members
    for g in groups:
        if len(g.get("member_source_urls", [])) < 2:
            return False, _SINGLE_MEMBER_GROUP_FEEDBACK

    # Missing or empty reasoning
    for g in groups:
        if not g.get("reasoning", ""):
            return False, _MISSING_REASONING_FEEDBACK
    for d in distinct:
        if not d.get("reasoning", ""):
            return False, _MISSING_REASONING_FEEDBACK
    for u in unresolved:
        if not u.get("reasoning", ""):
            return False, _MISSING_REASONING_FEEDBACK

    return True, None


def _build_evidence(
    raw: dict,
    no_def_urls: list[str],
    sources_checked: int,
    notes: str = "",
) -> SourcePluralityEvidence:
    """Build a SourcePluralityEvidence from a validated response dict."""
    groups = [
        DefinitionGroup(
            member_source_urls=g["member_source_urls"],
            shared_definition_label=g.get("shared_definition_label", ""),
            reasoning=g["reasoning"],
        )
        for g in raw.get("groups", [])
    ]
    distinct_sources = [
        DistinctFinding(source_url=d["source_url"], reasoning=d["reasoning"])
        for d in raw.get("distinct", [])
    ]
    unresolved = [
        UnresolvedFinding(source_url=u["source_url"], reasoning=u["reasoning"])
        for u in raw.get("unresolved", [])
    ]
    return SourcePluralityEvidence(
        sources_checked=sources_checked,
        groups=groups,
        distinct_sources=distinct_sources,
        unresolved=unresolved,
        no_definition_sources=no_def_urls,
        failed_reconciliation=[],
        notes=notes,
    )


def reconcile_sources(
    claim_text: str,
    findings: list[SourceFinding],
    llm_fn=None,
) -> SourcePluralityEvidence:
    """
    Reconcile definition-bearing SourceFindings and return a
    SourcePluralityEvidence that accounts for every input source exactly once.

    `llm_fn`, if provided, replaces the real OpenAI call. Signature:
        (claim_text: str, findings: list[dict], feedback: str | None) -> dict
    returning {"groups": [...], "distinct": [...], "unresolved": [...]}.
    feedback is None on the first call.
    """
    _llm_fn = llm_fn or _default_llm_call

    sources_checked = len(findings)

    # --- Step 1: deterministic split ---
    no_def_urls = [f.source_url for f in findings if not f.definition_found]
    candidates = [f for f in findings if f.definition_found]

    if len(candidates) == 0:
        return SourcePluralityEvidence(
            sources_checked=sources_checked,
            groups=[],
            distinct_sources=[],
            unresolved=[],
            no_definition_sources=no_def_urls,
            failed_reconciliation=[],
            notes="No sources had a stated definition.",
        )

    if len(candidates) == 1:
        return SourcePluralityEvidence(
            sources_checked=sources_checked,
            groups=[],
            distinct_sources=[
                DistinctFinding(
                    source_url=candidates[0].source_url,
                    reasoning=_SOLE_SOURCE_REASONING,
                )
            ],
            unresolved=[],
            no_definition_sources=no_def_urls,
            failed_reconciliation=[],
            notes="",
        )

    # --- Step 2 + 3: LLM call with validation + one retry ---
    candidate_dicts = [
        {
            "source_url": f.source_url,
            "claimed_value": f.claimed_value,
            "definition_text": f.definition_text,
        }
        for f in candidates
    ]
    expected_urls = {f.source_url for f in candidates}

    feedback: str | None = None

    for attempt in range(1, _MAX_RECONCILIATION_ATTEMPTS + 1):
        try:
            raw = _llm_fn(claim_text, candidate_dicts, feedback)
            if not isinstance(raw, dict):
                raise ValueError("response must be a dict")
        except Exception:
            feedback = _MALFORMED_JSON_FEEDBACK
            if attempt == _MAX_RECONCILIATION_ATTEMPTS:
                break
            continue

        is_valid, bad_feedback = _validate_response(raw, expected_urls)
        if is_valid:
            return _build_evidence(raw, no_def_urls, sources_checked)

        feedback = bad_feedback
        if attempt == _MAX_RECONCILIATION_ATTEMPTS:
            break

    # Both attempts failed: all candidates go to failed_reconciliation
    return SourcePluralityEvidence(
        sources_checked=sources_checked,
        groups=[],
        distinct_sources=[],
        unresolved=[],
        no_definition_sources=no_def_urls,
        failed_reconciliation=[f.source_url for f in candidates],
        notes="Reconciliation failed after all attempts.",
    )
