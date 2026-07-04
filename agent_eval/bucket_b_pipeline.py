"""Bucket B orchestrator: per-criterion search -> select -> fetch -> verify.

Given a company name and a list of NZIF criteria, runs the full chain for
each criterion independently and assembles the results into a ClaimTag with
bucket="B". Key decisions (full reasoning in adr/0015-bucket-b-pipeline.md):

- company_name is an explicit parameter, never inferred — a misparse would
  silently contaminate every search query with no visible failure.
- One criterion's failure never aborts the others; a failed criterion is
  omitted (never placeholded — CriterionEvidence requires all five fields to
  be real), and overall_status falls out of tag_schema's existing logic.
- The fetch cache is scoped to one call, never global: a global cache would
  serve stale content with no age signal and could cross-contaminate
  evidence between companies sharing a filing host or CDN.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, cast

from agent_eval.criterion_evidence import NZIF_CRITERIA, find_criterion_evidence
from agent_eval.domain_check import check_domain
from agent_eval.llm_client import default_complete_json
from agent_eval.log_utils import append_log_entry
from agent_eval.page_fetch import FetchResult, fetch_page_text
from agent_eval.tag_schema import ClaimTag, CriterionEvidence
from agent_eval.url_compare import same_url
from agent_eval.web_search import SearchResult, search_for_source

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
    search_results: list[SearchResult],
) -> dict:
    """
    Select the best candidate URL from search results for a given criterion.
    Returns {"url": str}. Tests inject a fake via the url_llm_fn parameter.
    """
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

    data = json.loads(default_complete_json(system, user))
    return {"url": data["url"]}


def run_bucket_b_pipeline(
    company_name: str,
    claim_id: str,
    allowlist: list[str],
    criteria: list[str] | None = None,
    *,
    search_fn: Callable[[str], list[SearchResult]] | None = None,
    url_llm_fn: Callable[[str, str, str, list[SearchResult]], dict] | None = None,
    fetch_fn: Callable[[str], FetchResult] | None = None,
    criterion_evidence_fn: Callable[..., dict] | None = None,
    log_dir: str = "logs",
) -> ClaimTag:
    """
    Run the full Bucket B evidence-gathering pipeline for `company_name`.

    For each criterion in `criteria` (defaults to all six NZIF criteria):
      1. Build a search query from company_name + the first clause of
         criterion_text (text before the first "."). This transformation was
         chosen over a bare criterion name and two other alternatives after
         live testing against all six criteria — the bare name returned zero
         results for every criterion in the first live run
         (adr/0015-bucket-b-pipeline.md).
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
            # success=True guarantees text is a str (FetchResult contract);
            # the cast is annotation-only.
            document = cast(str, fetch_result["text"])
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
