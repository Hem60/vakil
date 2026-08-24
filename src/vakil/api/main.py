"""HTTP surface: dispute webhooks in, case decisions out.

Deliberately thin. All judgement lives in vakil.decide; this module only
translates between HTTP and domain models, so nothing here needs a test that
the decision path does not already have.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request

from vakil.config import settings
from vakil.decide.pipeline import assess
from vakil.ingest.corpus import load_case, load_split
from vakil.ledger.chain import Ledger

app = FastAPI(title="Vakil", version="0.1.0")

CORPUS = Path("data/test")
LEDGER = Ledger("ledger.jsonl")


def _now() -> datetime:
    return datetime.now(UTC)


@app.get("/health")
def health() -> dict:
    ok, msg = LEDGER.verify()
    return {"status": "ok", "ledger": {"intact": ok, "detail": msg}}


@app.get("/cases")
def list_cases() -> list[dict]:
    out = []
    for case in load_split(CORPUS):
        result = assess(case, settings(), _now())
        out.append(
            {
                "case_id": case.case_id,
                "dispute_id": case.dispute.id,
                "reason_code": case.dispute.reason_code,
                "amount": case.dispute.amount,
                "verdict": result.decision.verdict,
                "p_win": round(result.p_win, 4),
                "net_ev": result.decision.ev.net_ev,
                "hours_left": round(result.sla.hours_left, 1),
            }
        )
    return out


@app.get("/cases/{case_id}")
def get_case(case_id: str) -> dict:
    path = CORPUS / f"{case_id}.json"
    if not path.exists():
        raise HTTPException(404, f"no such case: {case_id}")
    result = assess(load_case(path), settings(), _now())
    return result.to_ledger_payload()


@app.get("/ledger/verify")
def ledger_verify() -> dict:
    ok, msg = LEDGER.verify()
    if not ok:
        raise HTTPException(409, msg)
    return {"intact": True, "detail": msg}


@app.post("/webhooks/razorpay")
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str = Header(default=""),
) -> dict:
    """Razorpay signs webhooks with HMAC-SHA256 over the raw body.

    Verified before the payload is parsed, let alone acted on - an unsigned
    dispute event is an instruction from a stranger to spend money.
    """
    raw = await request.body()
    secret = settings().razorpay_webhook_secret
    if not secret:
        raise HTTPException(503, "webhook secret not configured")

    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, x_razorpay_signature):
        raise HTTPException(401, "bad signature")

    event = await request.json()
    LEDGER.append(
        dispute_id=event.get("payload", {}).get("dispute", {}).get("entity", {}).get("id", "?"),
        stage="webhook",
        payload={"event": event.get("event")},
        at=_now(),
    )
    return {"accepted": True}
