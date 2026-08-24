"""A stand-in for Razorpay test mode.

Disputes cannot be raised on demand against the real sandbox - they originate
with an issuing bank - so there is no way to exercise the filing path end to
end without something to file against. This mock speaks the documented shapes
of the Documents and Disputes APIs and nothing more. It is a test fixture, not
a simulation of Razorpay, and the real client in vakil.file.client is what
ships.

Endpoints mirrored:
    POST /v1/documents                 upload evidence, returns doc_xxx
    GET  /v1/disputes/{id}             fetch
    PATCH /v1/disputes/{id}            attach evidence
    POST /v1/disputes/{id}/contest     submit -> status under_review
"""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import FastAPI, HTTPException, UploadFile

app = FastAPI(title="mock-razorpay", version="0.1.0")

_documents: dict[str, dict[str, Any]] = {}
_disputes: dict[str, dict[str, Any]] = {}

VALID_EVIDENCE_FIELDS = {
    "shipping_proof",
    "billing_proof",
    "cancellation_proof",
    "customer_communication",
    "proof_of_service",
    "explanation_letter",
    "refund_confirmation",
    "access_activity_log",
    "refund_cancellation_policy",
    "term_and_conditions",
    "others",
}


@app.post("/v1/documents")
async def upload_document(file: UploadFile, purpose: str = "dispute_evidence") -> dict[str, Any]:
    doc_id = f"doc_{secrets.token_hex(8)}"
    body = await file.read()
    _documents[doc_id] = {
        "id": doc_id,
        "entity": "document",
        "purpose": purpose,
        "name": file.filename,
        "size": len(body),
        "mime_type": file.content_type,
    }
    return _documents[doc_id]


@app.put("/v1/_fixtures/disputes/{dispute_id}")
def seed_dispute(dispute_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Test-only: plant a dispute so the filing path has something to act on."""
    _disputes[dispute_id] = {**body, "id": dispute_id, "entity": "dispute"}
    return _disputes[dispute_id]


@app.get("/v1/disputes/{dispute_id}")
def fetch_dispute(dispute_id: str) -> dict[str, Any]:
    if dispute_id not in _disputes:
        raise HTTPException(
            400,
            {"error": {"code": "BAD_REQUEST_ERROR", "description": "no such dispute"}},
        )
    return _disputes[dispute_id]


@app.patch("/v1/disputes/{dispute_id}")
def attach_evidence(dispute_id: str, body: dict[str, Any]) -> dict[str, Any]:
    dispute = fetch_dispute(dispute_id)
    if dispute["status"] != "open":
        raise HTTPException(400, {"error": {"description": "dispute is not open"}})

    evidence = body.get("evidence", {})
    unknown = set(evidence) - VALID_EVIDENCE_FIELDS - {"summary", "amount"}
    if unknown:
        raise HTTPException(400, {"error": {"description": f"unknown evidence fields: {unknown}"}})

    for field, value in evidence.items():
        if field in {"summary", "amount"}:
            continue
        ids = value if isinstance(value, list) else [value]
        for doc_id in ids:
            if isinstance(doc_id, str) and doc_id not in _documents:
                raise HTTPException(400, {"error": {"description": f"unknown document {doc_id}"}})

    dispute.setdefault("evidence", {}).update(evidence)
    return dispute


@app.post("/v1/disputes/{dispute_id}/contest")
def contest(dispute_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    dispute = fetch_dispute(dispute_id)
    evidence = dispute.get("evidence", {})
    documents = [
        k for k, v in evidence.items() if k not in {"summary", "amount"} and v
    ]
    if not documents:
        # Mirrors the real constraint: at least one document id is required.
        raise HTTPException(400, {"error": {"description": "no evidence documents attached"}})

    action = (body or {}).get("action", "submit")
    if action == "submit":
        dispute["status"] = "under_review"
    return dispute


@app.post("/v1/disputes/{dispute_id}/accept")
def accept(dispute_id: str) -> dict[str, Any]:
    dispute = fetch_dispute(dispute_id)
    dispute["status"] = "lost"
    return dispute
