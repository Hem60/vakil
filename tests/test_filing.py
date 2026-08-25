"""Filing tests, driven against the bundled mock over an in-process ASGI
transport.

Not a hand-written fake: the requests go through the real `RazorpayClient` into
the real mock app, so the mock's own validation runs too. That catches the
class of bug where the client and the server each look correct in isolation and
disagree about the wire.

Most of these assert a refusal. This is the only module that can change
something at a payment provider, and nearly all of its behaviour is declining
to.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vakil.config import Settings
from vakil.decide.pipeline import assess
from vakil.draft.facts import build_fact_index
from vakil.draft.gate import ClaimKind, DraftedClaim, apply_gate
from vakil.draft.letter import TemplateDrafter, compose
from vakil.file.client import RazorpayClient, RazorpayError
from vakil.file.filing import (
    FILED_STAGE,
    FilingRefused,
    already_filed,
    check_gates,
    collect_documents,
    file_representment,
)
from vakil.file.mock_razorpay import app as mock_app
from vakil.ingest.corpus import load_case
from vakil.ledger.chain import Ledger
from vakil.models import Verdict
from vakil.rules.ce3 import qualifies_ce3

NOW = datetime(2026, 8, 24, 9, 0, tzinfo=UTC)
CASE = Path("data/test/case_0006.json")


@pytest.fixture
def client() -> RazorpayClient:
    """The real client, talking to the real mock, in-process.

    `TestClient` rather than `httpx.ASGITransport`: the latter is async-only,
    and this client is deliberately synchronous. TestClient subclasses
    httpx.Client and drives the ASGI app synchronously, so RazorpayClient sees
    exactly the interface it will see against the real API.
    """
    http = TestClient(mock_app)
    http.auth = ("id", "secret")
    return RazorpayClient(Settings(), http)


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path / "ledger.jsonl")


def case():
    if not CASE.exists():
        pytest.skip("corpus not generated - run make data")
    return load_case(CASE)


def assessment(**overrides: object):
    c = case()
    if overrides:
        c = c.__class__(**{**c.__dict__, **overrides})
    return assess(c, Settings(), NOW)


def letter_for(a):
    ce3 = qualifies_ce3(a.case.dispute, a.case.bundle, a.case.current)
    index = build_fact_index(a.case.dispute, a.case.bundle, ce3)
    return compose(TemplateDrafter(), a.case.dispute, index, a.requirements)


def seed(client: RazorpayClient, dispute_id: str, status: str = "open") -> None:
    """Plant a dispute in the mock so there is something to file against."""
    client._http().put(  # noqa: SLF001 - test fixture endpoint
        f"/v1/_fixtures/disputes/{dispute_id}",
        json={"status": status, "amount": 379800, "currency": "INR"},
    )


# ------------------------------------------------------------------- client


def test_uploading_a_document_returns_an_id(client: RazorpayClient, tmp_path: Path):
    path = tmp_path / "pod.pdf"
    path.write_bytes(b"%PDF-1.4 test")
    assert client.upload_document(path).startswith("doc_")


def test_uploading_a_file_that_is_not_there_fails_loudly(client: RazorpayClient):
    with pytest.raises(FileNotFoundError, match="not there"):
        client.upload_document(Path("does/not/exist.pdf"))


def test_evidence_fields_the_api_rejects_are_rejected_here_first(client: RazorpayClient):
    """Catching it locally names the field. Letting the API catch it returns a
    generic validation error after a round trip."""
    seed(client, "disp_x")
    with pytest.raises(RazorpayError, match="does not accept"):
        client.attach_evidence("disp_x", {"invented_field": ["doc_1"]})


def test_contesting_with_no_evidence_is_refused_by_the_api(client: RazorpayClient):
    seed(client, "disp_empty")
    with pytest.raises(RazorpayError, match="no evidence documents"):
        client.contest("disp_empty")


def test_a_full_round_trip_moves_the_dispute_to_under_review(
    client: RazorpayClient, tmp_path: Path
):
    seed(client, "disp_rt")
    path = tmp_path / "pod.pdf"
    path.write_bytes(b"%PDF-1.4 test")
    doc = client.upload_document(path)

    client.attach_evidence("disp_rt", {"shipping_proof": [doc], "summary": "s", "amount": 1})
    assert client.contest("disp_rt")["status"] == "under_review"


def test_attaching_an_unknown_document_id_is_refused(client: RazorpayClient):
    """The mock enforces what the real API does: evidence must reference
    documents that were actually uploaded."""
    seed(client, "disp_ghost")
    with pytest.raises(RazorpayError, match="unknown document"):
        client.attach_evidence("disp_ghost", {"shipping_proof": ["doc_never_uploaded"]})


# -------------------------------------------------------------------- gates


def test_a_folded_case_is_never_filed(ledger: Ledger):
    a = assessment()
    a.decision = a.decision.model_copy(update={"verdict": Verdict.FOLD})
    with pytest.raises(FilingRefused, match="not FIGHT"):
        check_gates(a, ledger)


def test_a_case_without_autonomy_is_queued_not_filed(ledger: Ledger):
    a = assessment()
    a.decision = a.decision.model_copy(update={"verdict": Verdict.FIGHT, "autofile": False})
    with pytest.raises(FilingRefused, match="queued for a human"):
        check_gates(a, ledger)


def test_an_expired_deadline_stops_the_filing(ledger: Ledger):
    """Money spent with no possible return."""
    a = assess(case(), Settings(), NOW + timedelta(days=400))
    a.decision = a.decision.model_copy(update={"verdict": Verdict.FIGHT, "autofile": True})
    with pytest.raises(FilingRefused, match="response window closed"):
        check_gates(a, ledger)


def test_a_dispute_already_in_the_ledger_is_not_filed_again(ledger: Ledger):
    a = assessment()
    a.decision = a.decision.model_copy(update={"verdict": Verdict.FIGHT, "autofile": True})
    ledger.append(
        dispute_id=a.case.dispute.id, stage=FILED_STAGE, payload={"status": "under_review"}, at=NOW
    )
    with pytest.raises(FilingRefused, match="Refusing to file twice"):
        check_gates(a, ledger)


def test_the_idempotency_check_reads_the_ledger_not_memory(ledger: Ledger, tmp_path: Path):
    """A retry after a crash is exactly when double-filing happens, and a crash
    is what destroys in-process state. So the guard has to be on disk."""
    ledger.append(dispute_id="disp_z", stage=FILED_STAGE, payload={}, at=NOW)
    reopened = Ledger(ledger.path)  # a fresh process would see this
    assert already_filed(reopened, "disp_z")
    assert not already_filed(reopened, "disp_other")


def test_a_different_stage_does_not_count_as_filed(ledger: Ledger):
    """Starting a filing is not completing one. The marker is written last on
    purpose, so a crash mid-flight leaves the dispute refileable."""
    ledger.append(dispute_id="disp_y", stage="filing_started", payload={}, at=NOW)
    assert not already_filed(ledger, "disp_y")


# ------------------------------------------------------------ end to end


def test_a_clean_case_files_and_reaches_under_review(client: RazorpayClient, ledger: Ledger):
    a = assessment()
    a.decision = a.decision.model_copy(update={"verdict": Verdict.FIGHT, "autofile": True})
    seed(client, a.case.dispute.id)

    result = file_representment(a, letter_for(a), client, ledger, NOW)

    assert result.status == "under_review"
    assert result.document_ids
    assert result.summary


def test_filing_records_the_submission_in_the_ledger(client: RazorpayClient, ledger: Ledger):
    a = assessment()
    a.decision = a.decision.model_copy(update={"verdict": Verdict.FIGHT, "autofile": True})
    seed(client, a.case.dispute.id)

    file_representment(a, letter_for(a), client, ledger, NOW)

    stages = [r["event"]["stage"] for r in ledger.records()]
    assert "filing_started" in stages
    assert FILED_STAGE in stages
    ok, _ = ledger.verify()
    assert ok


def test_filing_twice_is_refused_by_the_ledger(client: RazorpayClient, ledger: Ledger):
    a = assessment()
    a.decision = a.decision.model_copy(update={"verdict": Verdict.FIGHT, "autofile": True})
    seed(client, a.case.dispute.id)

    file_representment(a, letter_for(a), client, ledger, NOW)
    with pytest.raises(FilingRefused, match="Refusing to file twice"):
        file_representment(a, letter_for(a), client, ledger, NOW)


def test_an_empty_letter_is_not_filed(client: RazorpayClient, ledger: Ledger):
    """If the provenance gate stripped everything, there is no argument to
    make. Filing an empty one is worse than not filing."""
    a = assessment()
    a.decision = a.decision.model_copy(update={"verdict": Verdict.FIGHT, "autofile": True})
    seed(client, a.case.dispute.id)

    empty = apply_gate(
        (DraftedClaim(text="Delivered.", kind=ClaimKind.FACTUAL, cites=("nope.missing",)),),
        build_fact_index(
            a.case.dispute,
            a.case.bundle,
            qualifies_ce3(a.case.dispute, a.case.bundle, a.case.current),
        ),
    )
    with pytest.raises(FilingRefused, match="no letter to file"):
        file_representment(a, empty, client, ledger, NOW)


def test_documents_are_collected_only_for_evidence_actually_held():
    a = assessment()
    documents = collect_documents(a)
    if a.case.bundle.delivery:
        assert "shipping_proof" in documents
    if not a.case.bundle.refund_confirmation_uri:
        assert "refund_confirmation" not in documents
