"""
bucket_b_pipeline.py

Orchestrator for Bucket B: given a company name and a list of NZIF criteria
to check, runs the full search → URL selection → fetch → evidence extraction
chain for each criterion and assembles the results into a ClaimTag with
bucket="B".

DESIGN DECISIONS:

WHY company_name IS AN EXPLICIT PARAMETER, NOT INFERRED:

The company name is the anchor for every search query built here. If it were
inferred from the allowlist, the claim_text, or anywhere else, a misparse
would silently contaminate every search query built from it — producing
results for the wrong company without any visible failure. Requiring the
caller to supply it explicitly means the only way the company name can be
wrong is if the caller supplies the wrong one, which is visible at the call
site.

WHY ONE CRITERION'S FAILURE DOES NOT ABORT THE OTHERS:

Each criterion runs a fully independent chain. A search returning no results
for "disclosure" does not mean "ambition" will also return nothing — they are
different queries, different URLs, potentially different documents. Stopping
the whole run on the first failure would mean that a single temporarily
unavailable page or an obscure criterion with low search coverage would
prevent all other evidence from being gathered. Each criterion either produces
a CriterionEvidence record or is silently omitted; the final ClaimTag contains
whatever was found. This mirrors the independence already proven in
criterion_evidence.py's own tests.

WHY THE CACHE IS SCOPED TO ONE CALL, NOT GLOBAL:

The in-memory fetch cache exists only for the duration of one
run_bucket_b_pipeline() call. It is NOT persisted across calls, NOT stored as
a module-level variable, and NOT shared across different companies or runs.
Two reasons:

1. Staleness: a page fetched for a previous call may have changed. A global
   cache with no TTL would silently serve stale content to subsequent calls
   without any indication of age.

2. Company isolation: different companies can use the same CDN or filing
   domain (e.g. multiple companies filing to the same PDF host). Reusing a
   fetched document across companies would silently cross-contaminate evidence.

The cache's only job is to avoid redundant fetches WITHIN one call — a case
that is common (multiple criteria may share the same source document, e.g.
a sustainability report page) and entirely safe to deduplicate within a single
run.

WHY CriterionEvidence RECORDS ARE OMITTED (NOT PLACEHOLDED) FOR FAILED CRITERIA:

A failed criterion has no evidence_text, no evidence_source_url, and no
evidence_source_type. A placeholder record would require inventing values for
those fields or making them Optional — either breaks the existing CriterionEvidence
contract, which requires all five fields. Omitting the record is strictly
correct: criteria_evidence contains only real evidence. The overall_status
then correctly becomes "incomplete" if no criteria at all succeeded, and
"criteria_evidence_gathered" if any did, which is exactly the distinction
tag_schema already defines.
"""

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone

from dotenv import load_dotenv

from criterion_evidence import NZIF_CRITERIA, find_criterion_evidence
from domain_check import check_domain
from log_utils import append_log_entry
from page_fetch import fetch_page_text
from tag_schema import ClaimTag, CriterionEvidence
from url_compare import same_url
from web_search import search_for_source

load_dotenv()

MODEL = os.getenv("OPENAI_MODEL", "gpt-5-nano")

_ALL_CRITERIA = list(NZIF_CRITERIA.keys())


@dataclass
class BucketBAttemptRecord:
    """
    One criterion attempt within a run_bucket_b_pipeline call, written to the
    shared evaluation log. One entry per criterion regardless of where the chain
    stopped — mirrors AttemptRecord's one-entry-per-attempt granularity in
    extraction.py, which proved sufficient for diagnosing real bugs (including
    the search-query bug found in the first live run of this module).

    stage_reached values:
      "no_search_results"          — search returned nothing
      "url_not_from_search_results"— LLM proposed a URL not in search results
      "fetch_failed"               — page fetch failed
      "excerpt_not_verified"       — find_criterion_evidence returned
                                     not_found_after_retries
      "excerpt_verified"           — full chain succeeded
    """

    company_name: str
    criterion_name: str
    stage_reached: str
    status: str
    url: str
    timestamp: str


def _log_criterion_attempt(record: BucketBAttemptRecord, log_dir: str) -> None:
    """Append one criterion attempt to the shared evaluation log."""
    append_log_entry(
        {
            "timestamp": record.timestamp,
            "bucket": "B",
            "company_name": record.company_name,
            "criterion_name": record.criterion_name,
            "stage_reached": record.stage_reached,
            "status": record.status,
            "url": record.url,
        },
        log_dir,
    )


def _default_url_selection_llm_call(
    company_name: str,
    criterion_name: str,
    criterion_text: str,
    search_results: list[dict],
) -> dict:
    """
    Select the best candidate URL from search results for a given criterion.
    Returns {"url": str}. Tests inject a fake via the url_llm_fn parameter.
    """
    from openai import OpenAI

    client = OpenAI()

    candidates_text = "\n".join(
        f"{i + 1}. URL: {r['url']}\n   Title: {r['title']}\n   Snippet: {r['snippet']}"
        for i, r in enumerate(search_results)
    )

    system = (
        "You help find evidence for climate framework assessments. Given a "
        "company name, an NZIF alignment criterion, and a list of candidate "
        "URLs from a web search, select the single URL most likely to contain "
        "the company's own disclosure addressing that criterion. "
        'Respond with ONLY a JSON object with exactly one field: "url" '
        "(the URL of your chosen source, selected from the provided candidates). "
        "Only use URLs from the provided candidates list."
    )
    user = (
        f"Company: {company_name}\n"
        f"Criterion: {criterion_text}\n\n"
        f"Candidate sources from web search:\n{candidates_text}"
    )

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
    )
    data = json.loads(response.choices[0].message.content)
    return {"url": data["url"]}


def run_bucket_b_pipeline(
    company_name: str,
    claim_id: str,
    allowlist: list[str],
    criteria: list[str] | None = None,
    *,
    search_fn=None,
    url_llm_fn=None,
    fetch_fn=None,
    criterion_evidence_fn=None,
    log_dir: str = "logs",
) -> ClaimTag:
    """
    Run the full Bucket B evidence-gathering pipeline for `company_name`.

    For each criterion in `criteria` (defaults to all six NZIF criteria):
      1. Build a search query from company_name + the first clause of
         criterion_text (text before the first "."). The original query used
         company_name + criterion_name (e.g. "TSMC ambition"), which returned
         zero Tavily results across every criterion tested in the first live run
         — confirmed directly via raw Tavily API calls with the same parameters
         web_search.py uses, ruling out a bug in web_search.py or Tavily itself.
         Three alternatives were tested live before this one was chosen:
           - Full criterion_text: worked for "ambition" (5 results) but risks
             dilution on longer, more technical criteria text.
           - Hand-written per-criterion phrases: worked but requires manual
             tuning per criterion and does not scale.
           - First clause of criterion_text (split on "."): returned 5 results
             for all six real NZIF criteria in live Tavily calls, no exceptions.
             Chosen because it is a generic, mechanical transformation — no
             per-criterion hand-tuning, generalises automatically to any future
             criterion added to NZIF_CRITERIA.
      2. Run a web search for candidate URLs.
      3. Call an LLM to select the best candidate URL for this criterion.
      4. Verify the selected URL came from the search results (url_compare).
      5. Fetch the page text (with in-call caching to avoid duplicate fetches
         when multiple criteria share the same source URL).
      6. Call find_criterion_evidence to extract a verified excerpt.
      7. Determine evidence_source_type via check_domain against allowlist.
      8. Build a CriterionEvidence record on success; omit it on any failure.

    Each criterion's chain runs and fails independently. One criterion failing
    does not abort the others.

    Injectable fakes for testing (no real API calls needed in unit tests):

        search_fn(query: str) -> list[dict]
            Returns [{"url": str, "title": str, "snippet": str}, ...].

        url_llm_fn(company_name, criterion_name, criterion_text,
                   search_results) -> dict
            Returns {"url": str}.

        fetch_fn(url: str) -> dict
            Returns {"success": bool, "text": str|None, "content_type":
                     str|None, "failure_reason": str|None}.

        criterion_evidence_fn(document, criterion_name, criterion_text,
                              **kwargs) -> dict
            Replaces find_criterion_evidence entirely. Must accept the same
            positional arguments. Any extra keyword arguments are forwarded.

    Returns a ClaimTag with bucket="B" whose criteria_evidence list contains
    one CriterionEvidence per criterion that succeeded. overall_status is
    "criteria_evidence_gathered" if any evidence was found, "incomplete" if
    none was (computed by tag_schema, not here).
    """
    if criteria is None:
        criteria = _ALL_CRITERIA

    _search_fn = search_fn or search_for_source
    _url_llm_fn = url_llm_fn or _default_url_selection_llm_call
    _fetch_fn = fetch_fn or fetch_page_text
    _criterion_evidence_fn = criterion_evidence_fn or find_criterion_evidence

    # In-call fetch cache. Keyed by URL (exact string from search results).
    # Scoped to this call only — see module docstring for why it is not global.
    fetch_cache: dict[str, str] = {}

    gathered: list[CriterionEvidence] = []

    for criterion_name in criteria:
        criterion_text = NZIF_CRITERIA[criterion_name]
        first_clause = criterion_text.split(".")[0]
        query = f"{company_name} {first_clause}"
        _ts = datetime.now(timezone.utc).isoformat()

        # --- search ---
        search_results = _search_fn(query)
        if not search_results:
            _log_criterion_attempt(
                BucketBAttemptRecord(
                    company_name=company_name,
                    criterion_name=criterion_name,
                    stage_reached="no_search_results",
                    status="no_search_results",
                    url="",
                    timestamp=_ts,
                ),
                log_dir,
            )
            continue

        # --- URL selection ---
        url = ""
        try:
            proposal = _url_llm_fn(
                company_name, criterion_name, criterion_text, search_results
            )
            url = proposal["url"]
            if not isinstance(url, str):
                raise ValueError("url must be a string")
        except Exception:
            _log_criterion_attempt(
                BucketBAttemptRecord(
                    company_name=company_name,
                    criterion_name=criterion_name,
                    stage_reached="url_not_from_search_results",
                    status="malformed_llm_response",
                    url="",
                    timestamp=_ts,
                ),
                log_dir,
            )
            continue

        # --- url_compare gate ---
        if not any(same_url(url, r["url"]) for r in search_results):
            _log_criterion_attempt(
                BucketBAttemptRecord(
                    company_name=company_name,
                    criterion_name=criterion_name,
                    stage_reached="url_not_from_search_results",
                    status="url_not_from_search_results",
                    url=url,
                    timestamp=_ts,
                ),
                log_dir,
            )
            continue

        # --- fetch (with in-call cache) ---
        if url in fetch_cache:
            document = fetch_cache[url]
        else:
            fetch_result = _fetch_fn(url)
            if not fetch_result["success"]:
                _log_criterion_attempt(
                    BucketBAttemptRecord(
                        company_name=company_name,
                        criterion_name=criterion_name,
                        stage_reached="fetch_failed",
                        status=fetch_result["failure_reason"] or "fetch_failed",
                        url=url,
                        timestamp=_ts,
                    ),
                    log_dir,
                )
                continue
            document = fetch_result["text"]
            fetch_cache[url] = document

        # --- criterion evidence extraction ---
        result = _criterion_evidence_fn(document, criterion_name, criterion_text)
        if result["status"] != "excerpt_verified":
            _log_criterion_attempt(
                BucketBAttemptRecord(
                    company_name=company_name,
                    criterion_name=criterion_name,
                    stage_reached="excerpt_not_verified",
                    status=result["status"],
                    url=url,
                    timestamp=_ts,
                ),
                log_dir,
            )
            continue

        # --- evidence_source_type via domain check ---
        domain_result = check_domain(url, allowlist)
        source_type = "official" if domain_result["passed"] else "third_party"

        _log_criterion_attempt(
            BucketBAttemptRecord(
                company_name=company_name,
                criterion_name=criterion_name,
                stage_reached="excerpt_verified",
                status="excerpt_verified",
                url=url,
                timestamp=_ts,
            ),
            log_dir,
        )
        gathered.append(
            CriterionEvidence(
                criterion_name=criterion_name,
                criterion_text=criterion_text,
                evidence_text=result["excerpt"],
                evidence_source_url=url,
                evidence_source_type=source_type,
            )
        )

    return ClaimTag(
        claim_id=claim_id,
        claim_text=f"{company_name} NZIF alignment assessment",
        bucket="B",
        criteria_evidence=gathered if gathered else None,
    )
