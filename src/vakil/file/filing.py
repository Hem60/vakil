"""Filing a representment.

This is the only module in Vakil that can change something at a payment
provider, so nearly all of it is refusals.

Four gates stand between a decision and a submission, and every one of them
exists because of a specific way this could go wrong:

  1. **The verdict must be FIGHT.** Filing a case the engine decided to fold is
     not a bug in the engine, it is a bug here.
  2. **Autonomy must be granted.** `autofile` is false for large amounts and
     thin margins; those cases queue for a human with the pack assembled. A
     system that files everything it can is not bounded, whatever its docs say.
  3. **The deadline must be open.** Filing after the response window is spend
     with no possible return.
  4. **It must not already be filed.** Checked against the audit ledger, not
     against memory - a retry after a crash is exactly when double-filing
     happens, and memory is what the crash destroyed.

Only then does anything leave the process. Every step is written to the ledger
before and after, so a filing that half-completed is reconstructable rather
than mysterious.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from vakil.decide.pipeline import Assessment
from vakil.draft.gate import GateResult
from vakil.file.client import RazorpayClient, RazorpayError
from vakil.ledger.chain import Ledger
from vakil.models import Verdict
from vakil.rulebook.store import BUNDLE_TO_EVIDENCE_FIELD

#: Ledger stage marking a completed submission. The idempotency check looks for
#: this and nothing else.
FILED_STAGE = "filed"


class FilingRefused(RuntimeError):
    """A gate said no. Not an error condition - the expected outcome for most
    cases, and the reason is always specific."""


class FilingResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    dispute_id: str
    document_ids: dict[str, str]
    evidence: dict[str, Any]
    status: str
    summary: str


def already_filed(ledger: Ledger, dispute_id: str) -> bool:
    """Has this dispute been submitted before?

    Reads the ledger rather than any in-process state. The case that matters is
    a retry after a crash, and a crash is precisely what removes in-process
    state - so a memory-based guard is absent exactly when it is needed.
    """
    return any(
        event["stage"] == FILED_STAGE and event["dispute_id"] == dispute_id
        for event in (record["event"] for record in ledger.records())
    )


def check_gates(assessment: Assessment, ledger: Ledger) -> None:
    """Raise unless every condition for filing holds."""
    decision = assessment.decision

    if decision.verdict is not Verdict.FIGHT:
        raise FilingRefused(
            f"verdict is {decision.verdict}, not FIGHT - "
            f"{decision.rationale.split(';')[0]}"
        )

    if not decision.autofile:
        raise FilingRefused(
            f"autonomy not granted for this case (confidence {decision.confidence:.2f}, "
            f"amount Rs {assessment.case.dispute.amount / 100:,.2f}) - "
            "queued for a human with the pack assembled"
        )

    if not assessment.sla.is_actionable:
        raise FilingRefused(
            f"response window closed {abs(assessment.sla.hours_left):.1f}h ago - "
            "filing now would spend money with no possible return"
        )

    if already_filed(ledger, assessment.case.dispute.id):
        raise FilingRefused(
            "already filed - the ledger has a submission for this dispute. "
            "Refusing to file twice."
        )


def build_evidence(
    assessment: Assessment, letter: GateResult, document_ids: dict[str, str]
) -> dict[str, Any]:
    """Assemble Razorpay's evidence object.

    The mapping from a harvested evidence slot to an API field name is the one
    the rulebook already uses for gap analysis. Reusing it means the fields we
    file are exactly the fields the requirements were checked against - if those
    two ever drift apart, the gap report and the filing would disagree about
    what was submitted.
    """
    evidence: dict[str, Any] = {
        "summary": letter.body()[:1000],
        "amount": assessment.case.dispute.amount,
    }
    for field, document_id in document_ids.items():
        evidence.setdefault(field, []).append(document_id)
    return evidence


def collect_documents(assessment: Assessment) -> dict[str, Path]:
    """Which held documents go in, and under which API field.

    Only slots the harvest actually filled. A missing document produces no
    entry - the gap analysis already reported it, and filing an empty field
    would be claiming evidence that does not exist.
    """
    bundle = assessment.case.bundle
    documents: dict[str, Path] = {}

    if bundle.delivery and bundle.delivery.document_uri:
        for field in BUNDLE_TO_EVIDENCE_FIELD["delivery"]:
            documents[field] = Path("data") / bundle.delivery.document_uri
    if bundle.policy and bundle.policy.document_uri:
        documents[BUNDLE_TO_EVIDENCE_FIELD["policy"][0]] = Path("data") / bundle.policy.document_uri
    if bundle.refund_confirmation_uri:
        documents[BUNDLE_TO_EVIDENCE_FIELD["refund_confirmation_uri"][0]] = (
            Path("data") / bundle.refund_confirmation_uri
        )
    return documents


def file_representment(
    assessment: Assessment,
    letter: GateResult,
    client: RazorpayClient,
    ledger: Ledger,
    now: datetime,
    letter_path: Path | None = None,
) -> FilingResult:
    """Upload, attach, contest. Refuses unless all four gates pass."""
    check_gates(assessment, ledger)
    dispute_id = assessment.case.dispute.id

    if not letter.verified:
        raise FilingRefused(
            "the provenance gate stripped every claim - there is no letter to "
            "file, and filing an empty argument is worse than not filing"
        )

    ledger.append(
        dispute_id=dispute_id,
        stage="filing_started",
        payload={"claims": len(letter.verified), "stripped": len(letter.stripped)},
        at=now,
    )

    document_ids: dict[str, str] = {}
    for field, path in collect_documents(assessment).items():
        if not path.exists():
            # The document is referenced but not on disk. Skip it rather than
            # abort: a filing with three of four documents still argues the
            # case, and the ledger records what was left out.
            ledger.append(
                dispute_id=dispute_id,
                stage="document_missing",
                payload={"field": field, "path": str(path)},
                at=now,
            )
            continue
        document_ids[field] = client.upload_document(path)

    if letter_path and letter_path.exists():
        document_ids["explanation_letter"] = client.upload_document(letter_path)

    if not document_ids:
        raise FilingRefused(
            "no documents could be attached - Razorpay requires at least one, "
            "and a contest with no evidence is a refusal with extra steps"
        )

    evidence = build_evidence(assessment, letter, document_ids)

    try:
        client.attach_evidence(dispute_id, evidence)
        dispute = client.contest(dispute_id)
    except RazorpayError as exc:
        ledger.append(
            dispute_id=dispute_id,
            stage="filing_failed",
            payload={"status": exc.status, "detail": exc.detail},
            at=now,
        )
        raise

    result = FilingResult(
        dispute_id=dispute_id,
        document_ids=document_ids,
        evidence=evidence,
        status=str(dispute.get("status", "unknown")),
        summary=evidence["summary"],
    )

    # Written last, and it is what `already_filed` looks for. A crash before
    # this point leaves the dispute refileable, which is the safe direction:
    # a duplicate filing is worse than a retried one.
    ledger.append(
        dispute_id=dispute_id,
        stage=FILED_STAGE,
        payload={
            "status": result.status,
            "documents": document_ids,
            "evidence_fields": sorted(k for k in evidence if k not in {"summary", "amount"}),
        },
        at=now,
    )
    return result
