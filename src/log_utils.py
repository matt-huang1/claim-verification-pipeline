"""
log_utils.py

Shared append helper for the evaluation log. extraction.py (Bucket A),
bucket_b_pipeline.py (Bucket B), reconciliation.py (Bucket C), and
bucket_d_analysis.py (Bucket D) write to the same file so cross-bucket,
cross-company history is queryable in one place without manually merging files.
Every entry must include company_name and bucket as top-level fields so a
reader can filter or group by either dimension without bucket-specific parsing.

WHY A SEPARATE MODULE RATHER THAN DUPLICATED HELPERS:

The "append a JSON line, create the dir if needed" logic is identical in both
callers. Duplicating it would create two implementations that could diverge
silently (e.g. one caller changes the filename, the other doesn't). A single
shared helper is the same judgment call made for quote_match.py (used by both
extraction.py and criterion_evidence.py): if two modules need exactly the same
deterministic operation, the operation belongs in one place.

WHY evaluation_log.jsonl (NOT separate per-bucket files):

A per-bucket split (extraction.jsonl, bucket_b.jsonl) would require manual
merging to answer cross-bucket questions like "what URLs has this company's
pipeline touched?" or "which criteria tend to fail search vs. fetch?". A
single unified log with a bucket field answers these queries directly. The
bucket field makes splitting trivial after the fact if ever needed; merging
two separate files is harder.
"""

import json
import os

LOG_FILENAME = "evaluation_log.jsonl"


def append_log_entry(entry: dict, log_dir: str) -> None:
    """Append one JSON-lines entry to log_dir/evaluation_log.jsonl."""
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, LOG_FILENAME)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
