"""
Smoke tests for ground_truth.py — structure validation only, no pipeline calls.
"""

from agent_eval.ground_truth import COMPANY_CLAIMS

_REQUIRED_KEYS = {
    "allowlist",
    "bucket_a_claims",
    "bucket_b_notes",
    "bucket_c_claims",
    "bucket_d_claims",
}


def test_nine_companies_present():
    assert len(COMPANY_CLAIMS) == 9


def test_all_companies_have_required_keys():
    for company, entry in COMPANY_CLAIMS.items():
        assert (
            set(entry.keys()) == _REQUIRED_KEYS
        ), f"{company} has unexpected keys: {set(entry.keys())}"


def test_all_bucket_a_claims_have_required_fields():
    for company, entry in COMPANY_CLAIMS.items():
        for claim in entry["bucket_a_claims"]:
            for field in ("claim_text", "expected_source_domain", "notes"):
                assert (
                    field in claim
                ), f"{company} bucket_a_claims entry missing '{field}'"


def test_all_bucket_c_claims_have_required_fields():
    for company, entry in COMPANY_CLAIMS.items():
        for claim in entry["bucket_c_claims"]:
            for field in ("claim_text", "notes"):
                assert (
                    field in claim
                ), f"{company} bucket_c_claims entry missing '{field}'"


def test_all_bucket_d_claims_have_required_fields():
    for company, entry in COMPANY_CLAIMS.items():
        for claim in entry["bucket_d_claims"]:
            for field in ("claim_text", "notes"):
                assert (
                    field in claim
                ), f"{company} bucket_d_claims entry missing '{field}'"


def test_all_allowlists_are_non_empty():
    for company, entry in COMPANY_CLAIMS.items():
        assert len(entry["allowlist"]) >= 1, f"{company} has an empty allowlist"
