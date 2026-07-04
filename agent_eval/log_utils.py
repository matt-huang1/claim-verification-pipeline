"""Shared JSONL append helper for the unified evaluation log.

All four bucket pipelines write to one file so cross-bucket, cross-company
history is queryable without merging; every entry carries company_name and
bucket as top-level fields. One shared helper (rather than per-caller
copies) and one shared file (rather than per-bucket files) — reasoning in
adr/0016-unified-logging.md.
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
