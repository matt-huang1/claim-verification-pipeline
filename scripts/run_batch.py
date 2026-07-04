"""
run_batch.py

Runs a curated set of claims through the pipeline and writes serialised
results to data/results.json. Run directly:

    python scripts/run_batch.py

Not a test — uses real HTTP and LLM calls. Each run overwrites
data/results.json with the latest results.
"""

import json
import time
import traceback
from pathlib import Path
from typing import Any

from agent_eval.ground_truth import COMPANY_CLAIMS
from agent_eval.run_pipeline import run_pipeline
from agent_eval.serialisation import result_to_dict
from agent_eval.tpi_extract import fetch_and_tag_tpi_evidence

_ROOT = Path(__file__).parent.parent
_OUTPUT = _ROOT / "data" / "results.json"

# ─── curated claim set ────────────────────────────────────────────────────────

_CLAIMS: list[dict[str, Any]] = [
    {
        "label": "TSMC — RE100 commitment (Bucket A)",
        "company": "TSMC",
        "kind": "pipeline",
        "kwargs": {
            "claim_text": (
                "TSMC is moving its target for 100 percent renewable "
                "energy consumption for all global operations forward "
                "to 2040 from 2050"
            ),
            "allowlist": COMPANY_CLAIMS["tsmc"]["allowlist"],
            "company_name": "TSMC",
            "claim_id": "tsmc-a-re100",
        },
    },
    {
        "label": "TSMC — foundry market share (Bucket C)",
        "company": "TSMC",
        "kind": "pipeline",
        "kwargs": {
            "claim_text": "TSMC has roughly 60% of the foundry market",
            "allowlist": COMPANY_CLAIMS["tsmc"]["allowlist"],
            "company_name": "TSMC",
            "claim_id": "tsmc-c-foundry-market",
        },
    },
    {
        "label": "TSMC — NZIF criteria (Bucket B)",
        "company": "TSMC",
        "kind": "pipeline",
        "kwargs": {
            "claim_text": "TSMC is aligning to a net zero pathway under NZIF criteria",
            "allowlist": COMPANY_CLAIMS["tsmc"]["allowlist"],
            "company_name": "TSMC",
            "claim_id": "tsmc-b-nzif",
            "bucket": "B",
        },
    },
    {
        "label": "TotalEnergies — NZIF criteria (Bucket B)",
        "company": "TotalEnergies",
        "kind": "pipeline",
        "kwargs": {
            "claim_text": (
                "TotalEnergies is committed to aligning to a net zero pathway"
            ),
            "allowlist": COMPANY_CLAIMS["totalenergies"]["allowlist"],
            "company_name": "TotalEnergies",
            "claim_id": "totalenergies-b-nzif",
            "bucket": "B",
        },
    },
    {
        "label": "TotalEnergies — TPI Management Quality (Bucket B)",
        "company": "TotalEnergies",
        "kind": "tpi",
        "slug": "totalenergies",
        "claim_id": "totalenergies-b-tpi",
    },
    {
        "label": "Antofagasta — NZIF criteria (Bucket B)",
        "company": "Antofagasta",
        "kind": "pipeline",
        "kwargs": {
            "claim_text": (
                "Antofagasta is committed to aligning to a net zero pathway"
            ),
            "allowlist": COMPANY_CLAIMS["antofagasta"]["allowlist"],
            "company_name": "Antofagasta",
            "claim_id": "antofagasta-b-nzif",
            "bucket": "B",
        },
    },
    {
        "label": "Antofagasta — TPI Management Quality (Bucket B)",
        "company": "Antofagasta",
        "kind": "tpi",
        "slug": "antofagasta",
        "claim_id": "antofagasta-b-tpi",
    },
    {
        "label": "Frontier Lithium — absence of evidence (Bucket D)",
        "company": "Frontier Lithium",
        "kind": "pipeline",
        "kwargs": {
            "claim_text": (
                "Without verifiable emissions data or a stated net zero target, "
                "Frontier Lithium cannot currently be classified as a transition "
                "enabler — the inputs the frameworks require do not yet exist."
            ),
            "allowlist": COMPANY_CLAIMS["frontier_lithium"]["allowlist"],
            "company_name": "Frontier Lithium",
            "claim_id": "frontier-d-absence",
            "bucket": "D",
        },
    },
]


def _run_one(entry: dict) -> dict:
    """Run one entry and return a result dict with label and company fields."""
    result: dict
    if entry["kind"] == "tpi":
        tpi_result = fetch_and_tag_tpi_evidence(entry["claim_id"], entry["slug"])
        if tpi_result["success"]:
            tag = tpi_result["tag"]
            result = {
                "outcome": tag.overall_status,
                "bucket": "B",
                "triage_reasoning": None,
                "tag": tag,
            }
        else:
            result = {
                "outcome": f"tpi_fetch_failed:{tpi_result['failure_reason']}",
                "bucket": "B",
                "triage_reasoning": None,
                "tag": None,
            }
    else:
        result = dict(run_pipeline(**entry["kwargs"]))

    serialised = result_to_dict(result)
    serialised["label"] = entry["label"]
    serialised["company"] = entry["company"]
    return serialised


def main() -> None:
    total = len(_CLAIMS)
    all_results = []
    start = time.time()

    for n, entry in enumerate(_CLAIMS, 1):
        label = entry["label"]
        print(f"Running [{n}/{total}]: {label}...")
        try:
            serialised = _run_one(entry)
            outcome = serialised.get("outcome", "unknown")
            print(f"  → outcome: {outcome}")
            all_results.append(serialised)
        except Exception:
            print(f"  → ERROR running {label!r}:")
            traceback.print_exc()
            all_results.append(
                {
                    "label": label,
                    "company": entry.get("company", ""),
                    "outcome": "script_error",
                    "bucket": None,
                    "triage_reasoning": None,
                    "tag": None,
                }
            )

    elapsed = time.time() - start
    print(f"\nCompleted {total} claims in {elapsed:.1f}s")

    _OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(_OUTPUT, "w", encoding="utf-8") as fh:
        json.dump(all_results, fh, indent=2, ensure_ascii=False)
    print(f"Results written to {_OUTPUT}")


if __name__ == "__main__":
    main()
