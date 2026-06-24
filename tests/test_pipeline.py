"""
Tests for pipeline.py.

These cover the three-function split deliberately:
- build_bucket_a_tag is tested in isolation with hand-built fake check
  results, proving it runs no real checks (the reason it is separate from
  run_bucket_a_checks).
- verify_bucket_a_claim is tested end-to-end against the real TSMC case
  used throughout this project, plus the two failure paths the whole
  framework exists to catch: a hallucinated number, and a spoofed domain.
"""

from quote_match import match_quote
from tag_schema import ClaimTag
from pipeline import build_bucket_a_tag, verify_bucket_a_claim

# The real September 2023 TSMC RE100 press release text, as used across
# the rest of this project's tests.
TSMC_DOCUMENT = """
HSINCHU, Taiwan, R.O.C., Sep. 15, 2023 - To respond to climate change and
mitigate climate impact, TSMC (TWSE: 2330, NYSE: TSM) today announced an
acceleration of its RE100 sustainability timetable, moving its target for
100 percent renewable energy consumption for all global operations
forward to 2040 from 2050. TSMC also raised its 2030 target for
company-wide renewable energy consumption to 60 percent from 40 percent.
"""

TSMC_URL = "https://pr.tsmc.com/english/news/3067"
TSMC_ALLOWLIST = ["tsmc.com"]
TSMC_TRUE_QUOTE = (
    "moving its target for 100 percent renewable energy consumption "
    "for all global operations forward to 2040 from 2050"
)


def test_build_bucket_a_tag_uses_fake_inputs_without_running_checks():
    """
    build_bucket_a_tag is pure assembly. Hand-built domain_result and
    quote_result (the latter via match_quote on a tiny self-contained
    string, NOT via the real pipeline) are wrapped into a ClaimTag with
    no involvement from run_bucket_a_checks. This mirrors how
    test_tag_schema.py constructs ClaimTags directly.
    """
    fake_domain_result = {
        "domain": "pr.tsmc.com",
        "passed": True,
        "matched_entry": "tsmc.com",
    }
    fake_quote_result = match_quote(
        "renewable energy by 2040",
        "The company committed to renewable energy by 2040 in its report.",
    )

    tag = build_bucket_a_tag(
        claim_id="fake-1",
        claim_text="a hand-built claim",
        domain_result=fake_domain_result,
        quote_result=fake_quote_result,
    )

    assert isinstance(tag, ClaimTag)
    assert tag.bucket == "A"
    assert tag.claim_id == "fake-1"
    assert tag.domain_evidence.domain == "pr.tsmc.com"
    assert tag.domain_evidence.passed is True
    assert tag.quote_evidence.status == fake_quote_result.status
    assert tag.quote_evidence.candidate_count == len(fake_quote_result.candidates)


def test_verify_real_tsmc_claim_end_to_end_is_verified():
    """
    The motivating real-world case, traced end to end through the full
    pipeline: legitimate TSMC domain + the actual claimed quote present in
    the actual press release text => overall_status "verified".
    """
    tag = verify_bucket_a_claim(
        claim_id="tsmc-re100",
        claim_text="TSMC accelerated its 100% renewable target to 2040",
        url=TSMC_URL,
        allowlist=TSMC_ALLOWLIST,
        quote=TSMC_TRUE_QUOTE,
        document=TSMC_DOCUMENT,
    )
    assert tag.overall_status == "verified"


def test_verify_hallucinated_number_is_refused_end_to_end():
    """
    The full pipeline must refuse to verify a hallucinated claim, not just
    the quote_match unit. Same legitimate source, but the claimed quote
    says "2035" - a year that appears NOWHERE in the document. The numeric
    token gate fires through the whole stack: overall_status
    "numeric_mismatch", never "verified".
    """
    hallucinated_quote = (
        "moving its target for 100 percent renewable energy consumption "
        "for all global operations forward to 2035 from 2050"
    )
    tag = verify_bucket_a_claim(
        claim_id="tsmc-hallucinated",
        claim_text="TSMC accelerated its 100% renewable target to 2035",
        url=TSMC_URL,
        allowlist=TSMC_ALLOWLIST,
        quote=hallucinated_quote,
        document=TSMC_DOCUMENT,
    )
    assert tag.overall_status == "numeric_mismatch"
    assert tag.overall_status != "verified"


def test_verify_spoofed_domain_is_refused_even_with_real_quote():
    """
    A textually perfect quote match paired with a spoofed domain
    (tsmc.com.evil.com - the prefix-spoof pattern domain_check rejects)
    must not verify. The failed domain check overrides the perfect quote
    score: overall_status "source_illegitimate".
    """
    tag = verify_bucket_a_claim(
        claim_id="tsmc-spoofed",
        claim_text="TSMC accelerated its 100% renewable target to 2040",
        url="https://tsmc.com.evil.com/fake",
        allowlist=TSMC_ALLOWLIST,
        quote=TSMC_TRUE_QUOTE,
        document=TSMC_DOCUMENT,
    )
    assert tag.overall_status == "source_illegitimate"
    assert tag.overall_status != "verified"
