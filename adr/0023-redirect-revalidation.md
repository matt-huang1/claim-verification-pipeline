# ADR-0023: Re-validating the post-redirect URL

## Status

Accepted

## Context

`requests` follows HTTP redirects by default, and every verification layer
gated on the URL that was *requested*, not the URL the content actually came
from. The gap: a URL that passes the domain check can 302 off-domain (an open
redirect on a legitimate domain, a press release moved to a third-party CDN),
and the fetched document silently comes from elsewhere while the ClaimTag
records the domain check as passed. This is precisely the class of failure
the project exists to catch — a confident label ("verified", "official") not
actually earned by what it claims — found by external review rather than a
live run.

## Decision

- **`page_fetch.py` reports `final_url`** (the post-redirect `response.url`)
  in every successful `FetchResult`, and `None` on failure. The module stays
  generic: it knows nothing about allowlists; it just reports honestly where
  the content came from, the same contract as its named failure reasons.
- **Bucket A re-validates against `final_url`.** `extraction.py` (and
  `run_pipeline.py`'s tag rebuild) pass the post-redirect URL to
  `verify_bucket_a_claim`, so the domain evidence on the ClaimTag describes
  the document that was actually checked. An off-allowlist redirect now
  resolves to `source_illegitimate` instead of a wrongly-earned `verified`.
- **Buckets B and C label from `final_url`.** `evidence_source_type` /
  `source_type` are computed against the post-redirect URL, so content that
  arrived from off-domain is honestly labelled `third_party` rather than
  `official`. The allowlist still never gates B/C results (adr/0014).
- **Consumers fall back to the requested URL when `final_url` is absent**
  (`fetch_result.get("final_url") or url`), so injected test fakes that
  predate the field keep their exact previous behaviour.

## Consequences

- Same-domain redirects (http→https, `www.` variants, path moves within an
  allowlisted domain) behave exactly as before — the subdomain-suffix
  matching in `check_domain` is unchanged.
- A third-party URL redirecting *into* an allowlisted domain now passes the
  domain check, which is correct: the document genuinely came from the
  allowlisted host, and the quote match runs against that real content.
- The `url_compare` gate still runs against the URL the model proposed
  (membership in real search results is a property of the proposal, not of
  where the server ultimately serves the bytes).
- Locked in by tests: `fetch_page_text` surfaces the redirect target;
  an off-allowlist `final_url` produces `source_illegitimate` through both
  `extract_claim_evidence` and the dispatcher; Bucket B and C label
  redirected content `third_party`.
