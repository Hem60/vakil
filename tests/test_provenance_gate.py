"""Provenance gate tests.

This is the module that decides what gets written into a document filed with a
bank, so the tests are about what it refuses rather than what it allows.

The demo it exists to support: delete a courier document from a case and the
delivery sentence disappears from the letter rather than being invented. The
first test in the deletion section is that demo, asserted.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from vakil.draft.facts import FactIndex, build_fact_index, normalise
from vakil.draft.gate import ClaimKind, DraftedClaim, apply_gate
from vakil.models import (
    CE3Result,
    DeliveryProof,
    Dispute,
    EvidenceBundle,
    LineItem,
    Order,
    ReasonCode,
)

AT = datetime(2026, 8, 19, tzinfo=UTC)
SHIP = "62 Residency Lane, Jaipur 347264"


def dispute() -> Dispute:
    return Dispute(
        id="disp_1",
        payment_id="pay_1",
        amount=499900,
        reason_code=ReasonCode.NON_DELIVERY,
        respond_by=AT,
        created_at=AT,
    )


def order() -> Order:
    return Order(
        id="ord_0014",
        payment_id="pay_1",
        placed_at=AT,
        amount=499900,
        items=(LineItem(sku="SNK-1", title="Running Shoes", qty=1, unit_price=499900),),
        billing_address=SHIP,
        shipping_address=SHIP,
        customer_email="customer@example.com",
        customer_phone="+919800000000",
    )


def delivery() -> DeliveryProof:
    return DeliveryProof(
        tracking_id="EKA335441403",
        delivered_at=AT,
        signed_by="R. Kumar",
        delivered_to_address=SHIP,
        carrier="Ekart",
        document_uri="fixtures/pod/0014.pdf",
        source_spans={"delivered_at": "2026-08-19", "signed_by": "R. Kumar"},
    )


def bundle(*, with_delivery: bool = True) -> EvidenceBundle:
    return EvidenceBundle(
        dispute_id="disp_1",
        order=order(),
        delivery=delivery() if with_delivery else None,
    )


def index(*, with_delivery: bool = True) -> FactIndex:
    return build_fact_index(
        dispute(), bundle(with_delivery=with_delivery), CE3Result(qualifies=False, reason="n/a")
    )


def factual(text: str, cites: tuple[str, ...], asserts: tuple[str, ...] = ()) -> DraftedClaim:
    return DraftedClaim(text=text, kind=ClaimKind.FACTUAL, cites=cites, asserts=asserts)


def argument(text: str) -> DraftedClaim:
    return DraftedClaim(text=text, kind=ClaimKind.ARGUMENT)


# ------------------------------------------------------------- fact index


def test_index_carries_provenance_for_every_fact():
    for fact in index().facts:
        assert fact.source


def test_extraction_spans_reach_the_index():
    """The gate cites these back into the letter's audit trail. Without them a
    verified claim could not point at the words on the page."""
    fact = index().get("delivery.delivered_at")
    assert fact is not None
    assert fact.quote == "2026-08-19"


def test_absent_evidence_produces_no_fact_rather_than_a_placeholder():
    """No "unknown" entry, nothing a drafter could read as permission to
    speculate. The fact simply is not there."""
    assert index(with_delivery=False).get("delivery.delivered_at") is None


def test_index_supports_a_value_the_record_contains():
    assert index().supports("delivery.delivered_to_address", SHIP)


def test_index_rejects_a_value_the_record_does_not_contain():
    assert not index().supports("delivery.delivered_to_address", "9 Other Road, Mumbai")


def test_comparison_ignores_case_and_punctuation_only():
    assert normalise("62 Residency Lane, Jaipur") == "62 residency lane jaipur"


# ------------------------------------------------------------ verification


def test_a_cited_and_matching_claim_survives():
    claims = (
        factual(
            "The parcel was delivered on 2026-08-19.",
            cites=("delivery.delivered_at",),
            asserts=("2026-08-19",),
        ),
    )
    result = apply_gate(claims, index())
    assert result.verified
    assert result.verified[0].cited_span == "2026-08-19"


def test_a_factual_claim_with_no_citation_is_stripped():
    result = apply_gate((factual("The customer received the goods.", cites=()),), index())
    assert result.stripped
    assert "no citation" in result.stripped[0].note


def test_a_claim_citing_evidence_that_does_not_exist_is_stripped():
    claims = (factual("It was signed for.", cites=("delivery.signed_by",)),)
    result = apply_gate(claims, index(with_delivery=False))
    assert result.stripped
    assert "no evidence for" in result.stripped[0].note


def test_a_claim_asserting_the_wrong_value_is_stripped():
    """The most dangerous failure: a real citation attached to a wrong number.
    It looks sourced and is false."""
    claims = (
        factual(
            "The parcel was delivered on 2026-08-25.",
            cites=("delivery.delivered_at",),
            asserts=("2026-08-25",),
        ),
    )
    result = apply_gate(claims, index())
    assert result.stripped
    assert "but the record says" in result.stripped[0].note


def test_mismatched_citation_and_assertion_counts_are_stripped():
    claims = (
        factual(
            "Delivered on 2026-08-19 to 62 Residency Lane.",
            cites=("delivery.delivered_at", "delivery.delivered_to_address"),
            asserts=("2026-08-19",),
        ),
    )
    result = apply_gate(claims, index())
    assert result.stripped
    assert "cannot tell which value" in result.stripped[0].note


def test_one_bad_claim_does_not_take_the_letter_with_it():
    claims = (
        factual("Order ord_0014 was placed.", cites=("order.id",), asserts=("ord_0014",)),
        factual(
            "Delivered on 2026-08-25.",
            cites=("delivery.delivered_at",),
            asserts=("2026-08-25",),
        ),
        argument("The merchant asks that this dispute be resolved in its favour."),
    )
    result = apply_gate(claims, index())
    assert len(result.verified) == 2
    assert len(result.stripped) == 1


# ------------------------------------------------- non-factual sentences


def test_an_argument_sentence_needs_no_citation():
    result = apply_gate((argument("The merchant respectfully contests this dispute."),), index())
    assert result.verified


@pytest.mark.parametrize(
    "text",
    [
        "The goods arrived on 2026-08-19 as promised.",
        "The merchant refunded Rs 4,999 already.",
        "Tracking EKA335441403 confirms this.",
        "Dispute disp_1 has no merit.",
    ],
)
def test_an_argument_sentence_that_states_a_fact_is_stripped(text: str):
    """A factual claim can hide behind a non-factual label. Dates, amounts and
    identifiers are checkable, so a sentence carrying one is held to the
    factual standard whatever it calls itself."""
    result = apply_gate((argument(text),), index())
    assert result.stripped
    assert "disguise" in result.stripped[0].note


# ------------------------------------ deleting a document deletes the claim


def test_deleting_the_courier_document_removes_the_delivery_sentence():
    """The demo, asserted. Same claims, same drafter, one document withdrawn -
    and the sentence that depended on it leaves the letter rather than being
    invented, because the drafter has no mechanism for inventing."""
    claims = (
        factual("Order ord_0014 was placed.", cites=("order.id",), asserts=("ord_0014",)),
        factual(
            "The parcel was delivered on 2026-08-19 and signed for by R. Kumar.",
            cites=("delivery.delivered_at", "delivery.signed_by"),
            asserts=("2026-08-19", "R. Kumar"),
        ),
        argument("The merchant asks that this dispute be resolved in its favour."),
    )

    with_proof = apply_gate(claims, index())
    without_proof = apply_gate(claims, index(with_delivery=False))

    assert "signed for by R. Kumar" in with_proof.body()
    assert "signed for by R. Kumar" not in without_proof.body()
    assert "Order ord_0014 was placed." in without_proof.body()
    assert without_proof.strip_rate == pytest.approx(1 / 3)


def test_the_filed_body_contains_only_survivors():
    claims = (
        factual(
            "Delivered on 2026-08-25.",
            cites=("delivery.delivered_at",),
            asserts=("2026-08-25",),
        ),
        argument("The merchant contests this dispute."),
    )
    body = apply_gate(claims, index()).body()
    assert body == "The merchant contests this dispute."
    assert "2026-08-25" not in body


def test_an_empty_draft_produces_an_empty_letter_not_an_error():
    result = apply_gate((), index())
    assert result.body() == ""
    assert result.strip_rate == 0.0


def test_a_letter_can_be_stripped_to_nothing():
    """If every sentence fails, the correct output is no letter - not a letter
    of apologies with the facts removed."""
    claims = (factual("Delivered.", cites=("delivery.delivered_at",)),)
    result = apply_gate(claims, index(with_delivery=False))
    assert result.body() == ""
    assert result.strip_rate == 1.0
