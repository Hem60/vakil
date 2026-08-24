"""Rulebook tests.

Two things matter here. First, that requirements lookup is exact and cited -
this is the module that tells the drafting stage what may be argued. Second,
that gap analysis never invents a gap a merchant could not close, because a
false gap becomes a false claim of missing evidence in the console.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from vakil.models import DeliveryProof, EvidenceBundle, MerchantPolicy, Order, ReasonCode
from vakil.rulebook.search import BM25Retriever, tokenize
from vakil.rulebook.store import (
    Necessity,
    Rulebook,
    RulebookError,
    blocking_gaps,
    evidence_gaps,
    satisfied_evidence_fields,
)

AT = datetime(2026, 8, 24, tzinfo=UTC)
SHIP = "12 MG Road, Bengaluru 560001"


@pytest.fixture(scope="module")
def rulebook() -> Rulebook:
    return Rulebook.load()


@pytest.fixture(scope="module")
def retriever(rulebook: Rulebook) -> BM25Retriever:
    return BM25Retriever(rulebook)


def order() -> Order:
    return Order(
        id="ord_1",
        payment_id="pay_1",
        placed_at=AT,
        amount=499900,
        items=(),
        billing_address=SHIP,
        shipping_address=SHIP,
        customer_email="a@example.com",
        customer_phone="+919800000000",
    )


def delivery(signed: bool = True) -> DeliveryProof:
    return DeliveryProof(
        tracking_id="DEL123",
        delivered_at=AT,
        signed_by="R. Kumar" if signed else None,
        delivered_to_address=SHIP,
        carrier="Delhivery",
        document_uri="fixtures/pod/1.pdf",
    )


def policy() -> MerchantPolicy:
    return MerchantPolicy(
        refund_policy_text="7 days",
        terms_text="standard",
        effective_from=AT,
        document_uri="fixtures/policy/1.html",
    )


# --------------------------------------------------------------- loading


def test_every_rule_carries_a_citation(rulebook: Rulebook):
    """A requirement without a source is an assertion, and this system is not
    entitled to assert what a card network requires."""
    for rule in rulebook:
        assert rule.citation.document
        assert rule.citation.section
        assert rule.citation.url.startswith("http")


def test_every_reason_code_has_at_least_one_required_rule(rulebook: Rulebook):
    for code in ReasonCode:
        rules = rulebook.requirements_for(code)
        assert any(r.necessity is Necessity.REQUIRED for r in rules), code


def test_duplicate_rule_ids_are_rejected(rulebook: Rulebook):
    rules = list(rulebook)
    with pytest.raises(RulebookError, match="duplicate rule id"):
        Rulebook([rules[0], rules[0]])


def test_coverage_reports_unverified_rules(rulebook: Rulebook):
    """Most entries are summaries not yet checked against a licensed rulebook.
    That number must be visible, not assumed away."""
    coverage = rulebook.coverage()
    assert coverage["unverified"] > 0
    assert coverage["reason_codes_missing"] == []
    assert 0.0 <= coverage["verified_fraction"] <= 1.0


# ------------------------------------------------------------ retrieval


def test_requirements_are_ordered_required_first(rulebook: Rulebook):
    rules = rulebook.requirements_for(ReasonCode.NON_DELIVERY)
    necessities = [r.necessity for r in rules]
    assert necessities[0] is Necessity.REQUIRED
    assert necessities == sorted(
        necessities,
        key=lambda n: [
            Necessity.REQUIRED,
            Necessity.ALTERNATIVE,
            Necessity.SUPPORTING,
            Necessity.CONTEXT,
        ].index(n),
    )


def test_requirements_include_universal_rules(rulebook: Rulebook):
    ids = {r.id for r in rulebook.requirements_for(ReasonCode.DUPLICATE_PROCESSING)}
    assert "visa-all-rebuttal-letter" in ids


def test_requirements_exclude_other_reason_codes(rulebook: Rulebook):
    rules = rulebook.requirements_for(ReasonCode.CREDIT_NOT_PROCESSED)
    for rule in rules:
        assert rule.reason_code in (None, ReasonCode.CREDIT_NOT_PROCESSED)


def test_lookup_is_deterministic(rulebook: Rulebook):
    """The same dispute must produce the same cited requirements every time -
    this is why the mapping is a table and not a retriever."""
    first = [r.id for r in rulebook.requirements_for(ReasonCode.FRAUD_CARD_ABSENT)]
    second = [r.id for r in rulebook.requirements_for(ReasonCode.FRAUD_CARD_ABSENT)]
    assert first == second


# --------------------------------------------------------------- search


def test_search_finds_the_delivery_rule(retriever: BM25Retriever):
    hits = retriever.search("proof that the parcel was delivered and signed for")
    assert hits
    assert "delivery" in hits[0].rule.id


def test_search_filters_by_reason_code_rather_than_boosting(retriever: BM25Retriever):
    """Asking about 13.1 must never surface a 10.4-only rule, however close the
    wording - a citation to the wrong dispute condition is worse than none."""
    hits = retriever.search("delivery address", reason_code=ReasonCode.NON_DELIVERY)
    for hit in hits:
        assert hit.rule.reason_code in (None, ReasonCode.NON_DELIVERY)


def test_search_returns_nothing_for_a_stopword_only_query(retriever: BM25Retriever):
    assert retriever.search("the evidence of a dispute") == []


def test_tokenize_drops_stopwords_and_singletons():
    assert tokenize("The evidence of a delivery") == ["delivery"]


# ----------------------------------------------------------- gap analysis


def test_no_gaps_when_evidence_is_complete(rulebook: Rulebook):
    bundle = EvidenceBundle(
        dispute_id="d1", order=order(), delivery=delivery(), policy=policy()
    )
    rules = rulebook.requirements_for(ReasonCode.NON_DELIVERY)
    assert blocking_gaps(evidence_gaps(rules, bundle)) == []


def test_missing_delivery_proof_is_a_blocking_gap(rulebook: Rulebook):
    bundle = EvidenceBundle(dispute_id="d1", order=order())
    rules = rulebook.requirements_for(ReasonCode.NON_DELIVERY)
    blocking = blocking_gaps(evidence_gaps(rules, bundle))
    assert blocking
    assert "shipping_proof" in blocking[0].missing_fields
    assert blocking[0].citation.url


def test_alternatives_in_a_group_do_not_both_count_as_missing(rulebook: Rulebook):
    """Physical delivery proof and digital service logs are both 'required' for
    non-delivery, but one order can only ever produce one of them. Demanding
    both would manufacture a gap no merchant could close."""
    bundle = EvidenceBundle(dispute_id="d1", order=order(), delivery=delivery())
    rules = rulebook.requirements_for(ReasonCode.NON_DELIVERY)
    gap_ids = {g.rule_id for g in evidence_gaps(rules, bundle)}
    assert "visa-13.1-proof-of-service" not in gap_ids
    assert "visa-13.1-proof-of-delivery" not in gap_ids


def test_ce3_qualification_excuses_the_required_set(rulebook: Rulebook):
    bundle = EvidenceBundle(dispute_id="d1", order=order())
    rules = rulebook.requirements_for(ReasonCode.FRAUD_CARD_ABSENT)

    without = blocking_gaps(evidence_gaps(rules, bundle, ce3_qualifies=False))
    with_ce3 = blocking_gaps(evidence_gaps(rules, bundle, ce3_qualifies=True))

    assert without
    assert not with_ce3


def test_context_rules_never_produce_gaps(rulebook: Rulebook):
    """VAMP thresholds and RBI framing shape strategy; they are not documents
    anyone can attach to a filing."""
    bundle = EvidenceBundle(dispute_id="d1")
    rules = rulebook.requirements_for(ReasonCode.NON_DELIVERY)
    context_ids = {r.id for r in rules if r.necessity is Necessity.CONTEXT}
    gap_ids = {g.rule_id for g in evidence_gaps(rules, bundle)}
    assert not (context_ids & gap_ids)


def test_generated_fields_are_never_gaps(rulebook: Rulebook):
    """The rebuttal letter is required on every dispute, but Vakil writes it -
    reporting it as missing evidence would send the merchant looking for a
    document that does not exist yet."""
    bundle = EvidenceBundle(dispute_id="d1")
    rules = rulebook.requirements_for(ReasonCode.NON_DELIVERY)
    gap_ids = {g.rule_id for g in evidence_gaps(rules, bundle)}
    assert "visa-all-rebuttal-letter" not in gap_ids


def test_satisfied_fields_map_bundle_slots_to_razorpay_names():
    bundle = EvidenceBundle(dispute_id="d1", order=order(), delivery=delivery(), policy=policy())
    fields = satisfied_evidence_fields(bundle)
    assert {"billing_proof", "shipping_proof", "refund_cancellation_policy"} <= fields
    assert "refund_confirmation" not in fields
