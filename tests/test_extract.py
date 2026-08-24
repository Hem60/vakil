"""Document extraction tests.

Extraction is one of two places a model touches a case, so what gets tested
here is the boundary around it: that an illegible reading never becomes a fact,
that dates are parsed rather than coerced, and that a blank page cannot be
passed downstream as evidence. The model's own accuracy is measured by
evals/extraction_eval.py against real documents, not asserted here.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from vakil.config import Settings
from vakil.evidence.extract import (
    ClaudeExtractor,
    ExtractedField,
    ExtractedPOD,
    ExtractionResult,
    StubExtractor,
    parse_delivery_date,
    to_delivery_proof,
)


def field(value: str | None, legible: bool = True, quote: str | None = None) -> ExtractedField:
    return ExtractedField(
        value=value, source_quote=quote if quote is not None else value, legible=legible
    )


def pod(**overrides: ExtractedField) -> ExtractedPOD:
    base = {
        "tracking_id": field("EKA335441403"),
        "carrier": field("Ekart"),
        "delivered_at": field("2026-08-19"),
        "signed_by": field("R. Kumar"),
        "delivered_to_address": field("62 Residency Lane, Jaipur 347264"),
    }
    base.update(overrides)
    return ExtractedPOD(**base)  # type: ignore[arg-type]


def result(extracted: ExtractedPOD) -> ExtractionResult:
    return ExtractionResult(
        document_uri="fixtures/pod/0014.pdf", extracted=extracted, model="test"
    )


# --------------------------------------------------------------- date parsing


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-08-19", datetime(2026, 8, 19)),
        ("19/08/2026", datetime(2026, 8, 19)),
        ("19-08-2026", datetime(2026, 8, 19)),
    ],
)
def test_parses_the_formats_couriers_print(raw: str, expected: datetime):
    assert parse_delivery_date(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "sometime last week", "not a date", "??/??/????"])
def test_unparseable_dates_become_none_rather_than_a_guess(raw: str | None):
    """A wrong delivery date is worse than a missing one - it would be argued
    as fact in a filing to a bank."""
    assert parse_delivery_date(raw) is None


# ------------------------------------------------- illegible never becomes fact


def test_illegible_signature_does_not_become_a_signature():
    """The single most important line in this module. A smudged squiggle the
    model could not read must not end up asserted as a named recipient."""
    proof = to_delivery_proof(result(pod(signed_by=field("R. Kumar", legible=False))))
    assert proof is not None
    assert proof.signed_by is None


def test_explicit_no_signature_is_recorded_as_no_signature():
    """Distinct from illegible: the page clearly says nobody signed. That is a
    legible fact, and it is not a signature either."""
    proof = to_delivery_proof(
        result(pod(signed_by=field(None, legible=True, quote="LEFT AT DOOR - NO SIGNATURE")))
    )
    assert proof is not None
    assert proof.signed_by is None


def test_illegible_address_does_not_become_an_address():
    proof = to_delivery_proof(
        result(pod(delivered_to_address=field("62 Residency Lane", legible=False)))
    )
    assert proof is not None
    assert proof.delivered_to_address is None


# --------------------------------------------------------------- usable proof


def test_blank_page_yields_no_proof_at_all():
    """A POD with no tracking id and no date is not weak evidence, it is no
    evidence. Passing it on would let the win model score a blank page."""
    empty = pod(
        tracking_id=field(None, legible=False),
        delivered_at=field(None, legible=False),
        signed_by=field(None, legible=False),
        delivered_to_address=field(None, legible=False),
    )
    assert to_delivery_proof(result(empty)) is None


def test_partial_read_still_produces_proof():
    """A tracking id with an unreadable date is thin, but it is real evidence
    and the win model should get to weigh it."""
    partial = pod(delivered_at=field(None, legible=False), signed_by=field(None, legible=False))
    proof = to_delivery_proof(result(partial))
    assert proof is not None
    assert proof.tracking_id == "EKA335441403"
    assert proof.delivered_at is None


# ---------------------------------------------------------------- provenance


def test_source_spans_carry_every_field_that_was_read():
    """Day 7's provenance gate cannot verify a claim without these."""
    spans = pod().source_spans()
    assert set(spans) == {
        "tracking_id",
        "carrier",
        "delivered_at",
        "signed_by",
        "delivered_to_address",
    }


def test_source_spans_omit_fields_that_were_not_read():
    spans = pod(signed_by=field(None, legible=False)).source_spans()
    assert "signed_by" not in spans


def test_proof_carries_spans_through_to_the_domain_object():
    proof = to_delivery_proof(result(pod()))
    assert proof is not None
    assert proof.source_spans["tracking_id"] == "EKA335441403"


# ---------------------------------------------------------------- backends


def test_stub_needs_no_key_and_no_network():
    extracted = StubExtractor().extract(Path("fixtures/pod/0001.pdf"))
    assert extracted.model == "stub"
    assert extracted.input_tokens == 0


def test_stub_is_never_selected_automatically():
    """A silent stub fallback would make a broken key look like a working
    system that simply found nothing."""
    settings = Settings(anthropic_api_key="")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        ClaudeExtractor(settings).extract(Path("fixtures/pod/0001.pdf"))


def test_cost_is_computed_from_real_usage():
    r = ExtractionResult(
        document_uri="x.pdf", extracted=pod(), model="claude-opus-5",
        input_tokens=2_000, output_tokens=300,
    )
    # 2000 * $5/M + 300 * $25/M = $0.0175 -> about Rs 1.54
    assert 140 <= r.cost_paise(5.0, 25.0) <= 170
