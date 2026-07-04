"""
Property-based tests for the deterministic checks (Hypothesis).

The example-based suites lock in specific, historically-found
counterexamples; these properties assert the invariants that must hold for
EVERY input — the "would it give a different answer if the claim were
false" guarantees themselves. Deterministic by profile (derandomize=True,
no deadline) so CI never flakes on generation timing or seed variance.
See adr/0025-property-based-testing.md.
"""

import string

from hypothesis import assume, given, settings, strategies as st

from agent_eval.domain_check import check_domain
from agent_eval.quote_match import (
    MINIMUM_QUOTE_LENGTH_CHARS,
    _extract_numeric_tokens,
    match_quote,
)
from agent_eval.url_compare import same_url

settings.register_profile("deterministic", derandomize=True, deadline=None)
settings.load_profile("deterministic")

_VALID_STATUSES = {
    "unique",
    "ambiguous",
    "no_match",
    "numeric_mismatch",
    "quote_too_short",
}

# Hostname labels: lowercase alphanumerics only (no hyphens, no dots) —
# enough to exercise the matching logic without generating invalid DNS.
_label = st.text(
    alphabet=string.ascii_lowercase + string.digits, min_size=1, max_size=10
)

# Credential/port junk that must never influence a domain decision.
_cred = st.text(alphabet=string.ascii_lowercase + string.digits, min_size=1, max_size=8)


# ---------------------------------------------------------------------------
# quote_match invariants — hold for arbitrary text
# ---------------------------------------------------------------------------


@given(quote=st.text(max_size=120), document=st.text(max_size=600))
def test_status_is_always_from_the_named_vocabulary(quote, document):
    result = match_quote(quote, document)
    assert result.status in _VALID_STATUSES
    assert len(result.candidates) <= 3


@given(quote=st.text(max_size=120), document=st.text(max_size=600))
def test_candidate_spans_are_always_traceable_to_the_document(quote, document):
    """
    Every candidate's text must equal document[start_index:end_index] —
    the traceability guarantee the verification tag depends on. If this
    ever breaks, a human auditing a tag would be shown text that is not
    actually at the cited location.
    """
    result = match_quote(quote, document)
    for c in result.candidates:
        assert document[c.start_index : c.end_index] == c.text


@given(quote=st.text(max_size=120), document=st.text(max_size=600))
def test_unique_always_satisfies_the_numeric_token_gate(quote, document):
    """
    Soundness of the numeric gate for ANY input: a "unique" verdict is only
    ever issued when every numeric token in the claimed quote literally
    appears in the matched span.
    """
    result = match_quote(quote, document)
    if result.status == "unique":
        assert _extract_numeric_tokens(quote).issubset(
            _extract_numeric_tokens(result.candidates[0].text)
        )


@given(quote=st.text(max_size=200))
def test_short_quotes_are_always_rejected_before_matching(quote):
    assume(len(quote.strip()) < MINIMUM_QUOTE_LENGTH_CHARS)
    result = match_quote(quote, quote * 3)
    assert result.status == "quote_too_short"
    assert result.candidates == []


@given(
    figure=st.integers(min_value=1, max_value=999),
    year=st.integers(min_value=1990, max_value=2059),
    delta=st.integers(min_value=1, max_value=40),
)
def test_hallucinated_year_never_verifies(figure, year, delta):
    """
    Constructed numeric hallucination: the quote changes the document's year
    to one that appears nowhere in it. Whatever else the matcher concludes,
    it must never say "unique" — this is the discriminating property the
    whole project rests on (adr/0003).
    """
    wrong_year = year + delta
    assume(str(wrong_year) != str(figure))
    document = (
        f"The company committed to raising output to {figure} units "
        f"by {year}, according to its annual report."
    )
    honest_quote = f"raising output to {figure} units by {year}"
    corrupted_quote = f"raising output to {figure} units by {wrong_year}"

    assert match_quote(honest_quote, document).status == "unique"
    assert match_quote(corrupted_quote, document).status != "unique"


# ---------------------------------------------------------------------------
# domain_check invariants
# ---------------------------------------------------------------------------


@given(sub=_label, name=_label, tld=_label)
def test_true_subdomains_always_pass(sub, name, tld):
    entry = f"{name}.{tld}"
    result = check_domain(f"https://{sub}.{entry}/any/path", [entry])
    assert result["passed"] is True
    assert result["matched_entry"] == entry


@given(name=_label, tld=_label, attacker=_label, attacker_tld=_label)
def test_prefix_spoofs_never_pass(name, tld, attacker, attacker_tld):
    """
    A host that merely CONTAINS the allowlisted domain as a prefix
    (tsmc.com.evil.example) must always fail — for any generated names.
    """
    entry = f"{name}.{tld}"
    host = f"{entry}.{attacker}.{attacker_tld}"
    # If the attacker suffix itself happens to end with the entry, the host
    # genuinely IS a subdomain of the entry — exclude that legitimate case.
    assume(not f"{attacker}.{attacker_tld}".endswith(entry))
    assert check_domain(f"https://{host}/fake", [entry])["passed"] is False


@given(
    sub=_label,
    name=_label,
    tld=_label,
    user=_cred,
    pw=_cred,
    port=st.integers(min_value=1, max_value=65535),
)
def test_credentials_and_ports_never_change_the_domain_decision(
    sub, name, tld, user, pw, port
):
    """
    Metamorphic property targeting the netloc bug class (adr/0002): junk
    credentials and ports in the URL must never alter the pass/fail
    decision or the extracted domain.
    """
    entry = f"{name}.{tld}"
    host = f"{sub}.{entry}"
    plain = check_domain(f"https://{host}/path", [entry])
    dressed = check_domain(f"https://{user}:{pw}@{host}:{port}/path", [entry])
    assert dressed["passed"] == plain["passed"]
    assert dressed["domain"] == plain["domain"]


# ---------------------------------------------------------------------------
# url_compare invariants
# ---------------------------------------------------------------------------


@given(host=_label, tld=_label, path=_label)
def test_cosmetic_variants_always_compare_equal(host, tld, path):
    a = f"http://{host}.{tld}/{path}"
    b = f"https://www.{host}.{tld}/{path}/"
    assert same_url(a, b) is True


@given(host=_label, tld=_label, path=_label, extra=_label)
def test_a_different_path_never_compares_equal(host, tld, path, extra):
    a = f"https://{host}.{tld}/{path}"
    b = f"https://{host}.{tld}/{path}{extra}"
    assert same_url(a, b) is False
