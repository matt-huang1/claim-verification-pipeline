"""Bucket C per-source extraction: value + definition, each verified.

Given a document already in hand and a claim, propose the claimed value and
its stated scope definition, each independently verified against the real
document via quote_match — never trusted on the model's word alone.

Key decisions (full reasoning in adr/0014-source-extraction.md):

- Two independent honest-absence fields (value_found, definition_found): a
  source can state a figure without defining scope, or the reverse, and
  collapsing them into one flag would lose real partial information.
- is_literal_value is a form-based question (is there a literal digit?),
  never a confidence judgment — "roughly 60%" is True because "60%" is a
  digit, avoiding the hedge-word trap rejected in bucket_triage.py.
- Floor rule: a finding is kept if at least one of the two fields verified;
  a source contributing nothing checkable is omitted, not placeholded.
- No retry loop: a failed verification is itself the real result here, not
  an intermediate step — unlike extraction.py's fixable-proposal retries.
- allowlist only labels source_type ("official"/"third_party"); it never
  gates a result. Mostly-third-party findings are the expected, correct
  outcome for a definitionally contested claim.
"""

import json
from typing import Callable, cast

from agent_eval.domain_check import check_domain
from agent_eval.llm_client import default_complete_json
from agent_eval.page_fetch import FetchResult, fetch_page_text
from agent_eval.quote_match import match_quote
from agent_eval.tag_schema import SourceFinding
from agent_eval.url_compare import same_url
from agent_eval.web_search import SearchResult, search_for_source

# Total attempt cap = target_source_count * this multiplier. Stops the loop
# when a claim genuinely has few findable sources, without bounding target_count
# itself. Same "documented starting assumption" character as NO_PROGRESS_SCORE_DELTA
# in extraction.py — revisit once real runs produce real data.
_ATTEMPTS_MULTIPLIER = 3

_FINDING_SYSTEM_PROMPT = """\
You extract two specific pieces of information from a document about a claim.

CLAIM: you will be given a claim asserting a value about some market or category.

YOUR JOB: find (1) the claimed value itself, and (2) any stated definition or \
scope description for the category the value applies to. Each is independent — \
a source may have one, both, or neither.

FIELD DEFINITIONS:

  value_found (bool): true if the document states a specific value or figure \
related to the claim's subject. false if not.

  claimed_value (str or null): if value_found is true, copy the verbatim text \
from the document that states the value. If false, null.

  is_literal_value (bool): PURELY A FORM-BASED QUESTION — not a confidence \
or vagueness judgment. true if the claimed value appears in the document as a \
literal digit or percentage (e.g. "60%", "3.2 billion", "USD 20.4 billion"). \
false if it appears as a word-stated approximation ("roughly half", "around \
two thirds") or a qualitative description ("dominant", "majority share", \
"leading position"). false — not null — when value_found is false.

  IMPORTANT: is_literal_value is about HOW the number appears in the text \
(digit vs. word), not about how certain or fuzzy the claim sounds. A hedged \
claim ("approximately 60%") is still is_literal_value=true because "60%" is a \
literal digit. A confident-sounding claim ("TSMC dominates the market") is \
is_literal_value=false because there is no digit. Do not confuse form with \
certainty.

  definition_found (bool): true if the document explicitly defines or \
describes the scope or boundaries of the category the value applies to \
(e.g. "pure-play foundry market", "merchant foundry market excluding IDMs", \
"global semiconductor foundry services"). false if no such definition or \
scope statement appears.

  definition_text (str or null): if definition_found is true, copy the \
verbatim text from the document that defines or describes the scope. \
If false, null.

Respond with ONLY a JSON object with exactly these five fields: \
"value_found", "claimed_value", "is_literal_value", "definition_found", \
"definition_text".
"""


def _default_finding_llm_call(document: str, claim_text: str) -> dict:
    """Real LLM call for find_source_finding. Tests inject a fake via llm_fn."""
    return json.loads(
        default_complete_json(
            _FINDING_SYSTEM_PROMPT, f"Claim: {claim_text}\n\nDocument:\n{document}"
        )
    )


def find_source_finding(
    document: str,
    claim_text: str,
    source_url: str,
    source_type: str,
    llm_fn: Callable[[str, str], dict] | None = None,
) -> SourceFinding | None:
    """
    Extract and verify a claimed value and definition from `document`.

    Calls the LLM once. Defensively parses the response — malformed JSON or
    missing required fields yields None, not an uncaught exception.

    If value_found=True, runs quote_match on claimed_value.
    If definition_found=True, runs quote_match on definition_text.
    Both checks run independently of each other.

    Returns None if:
      - The LLM response is malformed (missing fields, bad types, bad JSON).
      - Neither value_verification_status nor definition_verification_status
        is "unique" (the floor rule: no verified content from this source).

    Returns a SourceFinding if at least one of value or definition is
    independently verified ("unique" quote_match status).

    `llm_fn`, if provided, replaces the real OpenAI call. Signature:
        (document: str, claim_text: str) -> dict
    returning the five-field response dict.
    """
    llm_fn = llm_fn or _default_finding_llm_call

    try:
        response = llm_fn(document, claim_text)
        value_found = response["value_found"]
        claimed_value = response["claimed_value"]
        is_literal_value = response["is_literal_value"]
        definition_found = response["definition_found"]
        definition_text = response["definition_text"]

        if not isinstance(value_found, bool):
            raise ValueError("value_found must be a bool")
        if not isinstance(definition_found, bool):
            raise ValueError("definition_found must be a bool")
        if not isinstance(is_literal_value, bool):
            raise ValueError("is_literal_value must be a bool")
        if value_found and not isinstance(claimed_value, str):
            raise ValueError("claimed_value must be a string when value_found is true")
        if definition_found and not isinstance(definition_text, str):
            raise ValueError(
                "definition_text must be a string when definition_found is true"
            )
    except Exception:
        return None

    # --- verify value, if proposed ---
    value_verification_status: str | None = None
    if value_found:
        qm = match_quote(claimed_value, document)
        value_verification_status = qm.status

    # is_literal_value is False (not None) when value_found=False, as received
    # from the model. If the model incorrectly sends True when value_found=False,
    # override it to False — the field is meaningless without a value.
    effective_is_literal = is_literal_value if value_found else False

    # --- verify definition, independently ---
    definition_verification_status: str | None = None
    if definition_found:
        qm_def = match_quote(definition_text, document)
        definition_verification_status = qm_def.status

    # --- floor rule: at least one verified result required ---
    if (
        value_verification_status != "unique"
        and definition_verification_status != "unique"
    ):
        return None

    return SourceFinding(
        source_url=source_url,
        source_type=source_type,
        value_found=value_found,
        claimed_value=claimed_value if value_found else None,
        is_literal_value=effective_is_literal,
        value_verification_status=value_verification_status,
        definition_found=definition_found,
        definition_text=definition_text if definition_found else None,
        definition_verification_status=definition_verification_status,
    )


_URL_SELECTION_SYSTEM_PROMPT = (
    "You help find sources for factual claims. Given a claim and a list of "
    "candidate URLs from a web search, select the single URL most likely to "
    "contain a stated value or definition relevant to the claim. "
    'Respond with ONLY a JSON object with exactly one field: "url" '
    "(the URL of your chosen source, selected from the provided candidates). "
    "Only use URLs from the provided candidates list."
)


def _default_url_selection_llm_call(
    claim_text: str,
    search_results: list[SearchResult],
) -> dict:
    """Select the best candidate URL from search results. Tests inject a fake."""
    candidates_text = "\n".join(
        f"{i + 1}. URL: {r['url']}\n   Title: {r['title']}\n   Snippet: {r['snippet']}"
        for i, r in enumerate(search_results)
    )

    data = json.loads(
        default_complete_json(
            _URL_SELECTION_SYSTEM_PROMPT,
            f"Claim: {claim_text}\n\n"
            f"Candidate sources from web search:\n{candidates_text}",
        )
    )
    return {"url": data["url"]}


def gather_source_findings(
    claim_text: str,
    allowlist: list[str],
    target_source_count: int = 5,
    search_fn: Callable[[str], list[SearchResult]] | None = None,
    url_llm_fn: Callable[[str, list[SearchResult]], dict] | None = None,
    fetch_fn: Callable[[str], FetchResult] | None = None,
    finding_llm_fn: Callable[[str, str], dict] | None = None,
) -> list[SourceFinding]:
    """
    Gather up to `target_source_count` verified SourceFindings for `claim_text`.

    Each iteration: search → select URL (LLM) → url_compare gate → fetch
    (with in-call cache) → find_source_finding. An iteration that produces
    a real SourceFinding (not None) counts toward target_source_count. An
    iteration that fails at any stage does not count toward the target but
    does count toward a hard cap of target_source_count * _ATTEMPTS_MULTIPLIER
    total attempts. If the cap is reached before the target, whatever real
    findings were gathered are returned (possibly an empty list).

    The in-call fetch cache avoids wasting a slot on a URL that search returns
    twice. It is scoped to one call only — never global, never persisted across
    calls. See bucket_b_pipeline.py's module docstring for the full reasoning
    (stale content, company isolation).

    Findings are deduplicated by source_url before returning — two findings
    from the same URL are not two independent sources for reconciliation
    purposes.

    ALLOWLIST NOTE: for Bucket C, allowlist is used only to determine
    source_type ("official" vs "third_party") on each SourceFinding, not to
    gate or reject results. Expecting most/all findings to be "third_party"
    on a definitionally contested claim is correct behavior, not a failure.
    See module docstring for the full open-question note.

    Injectable fakes for testing:
        search_fn(query: str) -> list[dict]
        url_llm_fn(claim_text, search_results) -> {"url": str}
        fetch_fn(url: str) -> {"success": bool, "text": str|None, ...}
        finding_llm_fn(document, claim_text) -> dict (five-field response)
    """
    _search_fn = search_fn or search_for_source
    _url_llm_fn = url_llm_fn or _default_url_selection_llm_call
    _fetch_fn = fetch_fn or fetch_page_text
    _finding_llm_fn = finding_llm_fn or _default_finding_llm_call

    # In-call fetch cache. Keyed by exact URL string from search results.
    # Scoped to this call only — see module docstring.
    fetch_cache: dict[str, str] = {}

    findings: list[SourceFinding] = []
    total_attempts = 0
    hard_cap = target_source_count * _ATTEMPTS_MULTIPLIER

    while len(findings) < target_source_count and total_attempts < hard_cap:
        total_attempts += 1

        # --- search ---
        search_results = _search_fn(claim_text)
        if not search_results:
            continue

        # --- URL selection ---
        try:
            proposal = _url_llm_fn(claim_text, search_results)
            url = proposal["url"]
            if not isinstance(url, str):
                raise ValueError("url must be a string")
        except Exception:
            continue

        # --- url_compare gate ---
        if not any(same_url(url, r["url"]) for r in search_results):
            continue

        # --- in-call URL deduplication ---
        if url in fetch_cache:
            document = fetch_cache[url]
        else:
            # --- fetch ---
            fetch_result = _fetch_fn(url)
            if not fetch_result["success"]:
                continue
            # success=True guarantees text is a str (FetchResult contract);
            # the cast is annotation-only.
            document = cast(str, fetch_result["text"])
            fetch_cache[url] = document

        # --- source type ---
        domain_result = check_domain(url, allowlist)
        source_type = "official" if domain_result["passed"] else "third_party"

        # --- find_source_finding ---
        finding = find_source_finding(
            document=document,
            claim_text=claim_text,
            source_url=url,
            source_type=source_type,
            llm_fn=_finding_llm_fn,
        )
        if finding is not None:
            findings.append(finding)

    seen_urls: set[str] = set()
    deduplicated: list[SourceFinding] = []
    for f in findings:
        if f.source_url not in seen_urls:
            seen_urls.add(f.source_url)
            deduplicated.append(f)
    return deduplicated
