"""
source_extraction.py

Bucket C per-source extraction: given a document already in hand (same
"document already fetched" assumption as criterion_evidence.py) and a claim,
propose a claimed value and its stated definition, each independently
verified against the real document via quote_match — never trusted on the
model's word alone.

TWO-FIELD HONEST-ABSENCE DESIGN:

A model needs a legitimate way to say "no value found" AND a separate
legitimate way to say "no definition found." These are genuinely independent:
a source can state a figure without defining its scope ("TSMC held 60% of the
market"), or define a scope without quoting a figure, or do both, or neither.
Collapsing them into one "found" flag would force the model to report "not
found" whenever either field is absent, losing real, partial information.
This is the same design as criterion_evidence.py's `found: false` — applied
twice over rather than once.

WHY is_literal_value IS A FORM-BASED QUESTION, NOT A CONFIDENCE JUDGMENT:

is_literal_value asks whether the claimed value appears in the text as a
literal digit or percentage (e.g. "60%") versus a word-stated approximation
("roughly half") or qualitative description ("dominant share"). This is a
mechanical, surface-form question — not a judgment about the claim's
certainty or vagueness. The distinction matters because a human reviewing
multiple sources needs to see, at a glance, whether sources are quoting real
figures or only qualitative descriptions — but mixing "the number was fuzzy"
with "the number was a word, not a digit" would conflate a confidence
judgment with a form judgment. Only the form judgment belongs here; the
system prompt explicitly states this to avoid the same hedge-word-misleads-
classification trap already identified and rejected in bucket_triage.py.

FLOOR RULE — why "partial success" is still a real finding:

A source that states a definition but no value, or a value that passes
quote_match but whose definition fails, is still genuinely informative for
Bucket C: it contributes real evidence about how one source defines the
scope even if it doesn't complete the picture. The floor is only applied
when NEITHER field produced a verified result — that source truly contributed
nothing checkable, and is omitted rather than placeholded.

NO RETRY LOOP IN find_source_finding:

A single quote_match rejection is recorded honestly in verification_status
as useful real information (a source that claimed something unverifiable is
itself informative), not something to retry past. This differs from
criterion_evidence.py (which retries because "not found" might mean the model
read too quickly) and extraction.py (which retries because a bad URL or quote
is fixable by trying differently). Here there is no retry because the finding
— including any failed verification — is the real result, not an intermediate
step on the way to a different result.

ALLOWLIST FOR BUCKET C — AN OPEN QUESTION EXPLICITLY FLAGGED:

In Bucket A and B, allowlist means "domains that count as the company's own
official disclosure" — and a verified source on an off-allowlist domain counts
against the claim. For Bucket C, the character of the evidence is different:
there is no single authoritative source by definition, and third-party analyst
reports (IC Insights, TrendForce, IDC) are the natural, expected evidence.
The allowlist here is used only to determine source_type ("official" vs
"third_party") on each SourceFinding — not to gate or reject any result.
A Bucket C live test with TSMC's market-share claim should expect most or
all findings to be "third_party," and that is correct, not a failure.
This differs enough from Bucket A's use of allowlist that it is worth naming
explicitly rather than silently inheriting the same pattern.
"""

import json
import os

from dotenv import load_dotenv

from domain_check import check_domain
from page_fetch import fetch_page_text
from quote_match import match_quote
from tag_schema import SourceFinding
from url_compare import same_url
from web_search import search_for_source

load_dotenv()

MODEL = os.getenv("OPENAI_MODEL", "gpt-5-nano")

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
    from openai import OpenAI

    client = OpenAI()
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": _FINDING_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Claim: {claim_text}\n\nDocument:\n{document}",
            },
        ],
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


def find_source_finding(
    document: str,
    claim_text: str,
    source_url: str,
    source_type: str,
    llm_fn=None,
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
    search_results: list[dict],
) -> dict:
    """Select the best candidate URL from search results. Tests inject a fake."""
    from openai import OpenAI

    client = OpenAI()

    candidates_text = "\n".join(
        f"{i + 1}. URL: {r['url']}\n   Title: {r['title']}\n   Snippet: {r['snippet']}"
        for i, r in enumerate(search_results)
    )

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": _URL_SELECTION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Claim: {claim_text}\n\n"
                    f"Candidate sources from web search:\n{candidates_text}"
                ),
            },
        ],
        response_format={"type": "json_object"},
    )
    data = json.loads(response.choices[0].message.content)
    return {"url": data["url"]}


def gather_source_findings(
    claim_text: str,
    allowlist: list[str],
    target_source_count: int = 5,
    search_fn=None,
    url_llm_fn=None,
    fetch_fn=None,
    finding_llm_fn=None,
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
            document = fetch_result["text"]
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

    return findings
