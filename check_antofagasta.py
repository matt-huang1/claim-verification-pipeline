import sys
sys.path.insert(0, 'src')

from run_pipeline import run_pipeline
from bucket_b_pipeline import run_bucket_b_pipeline
from tpi_extract import fetch_and_tag_tpi_evidence
from review import format_result, format_tag

ALLOWLIST = ["antofagasta.co.uk", "antofagasta.com"]

print("\n" + "="*60)
print("STEP 1 — Bucket A via full pipeline")
print("="*60)
result = run_pipeline(
    "Antofagasta achieved its 30% Scope 1 and 2 reduction target early "
    "through 100% renewable electricity contracts from April 2022",
    ALLOWLIST,
    company_name="Antofagasta",
    claim_id="antofagasta-a-001",
)
print(format_result(result))

print("\n" + "="*60)
print("STEP 2 — Bucket B NZIF")
print("="*60)
tag = run_bucket_b_pipeline(
    company_name="Antofagasta",
    claim_id="antofagasta-b-001",
    allowlist=ALLOWLIST,
)
print(format_tag(tag))

print("\n" + "="*60)
print("STEP 3 — TPI")
print("="*60)
result = fetch_and_tag_tpi_evidence("antofagasta-b-tpi-001", "antofagasta")
if result["success"]:
    print(format_tag(result["tag"]))
else:
    print(f"TPI fetch failed: {result['failure_reason']}")
