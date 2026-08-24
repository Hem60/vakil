"""Load synthetic corpus cases into domain models.

The corpus is the offline mirror of what the evidence harvest produces at
runtime. Keeping one loader means the eval harness and the live pipeline
consume identical shapes, so a number measured offline means something online.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from vakil.models import (
    DeliveryProof,
    DeviceFingerprint,
    Dispute,
    EvidenceBundle,
    LineItem,
    MerchantPolicy,
    Order,
    PriorTransaction,
    SupportMessage,
    SupportThread,
)


@dataclass(frozen=True)
class Case:
    case_id: str
    dispute: Dispute
    bundle: EvidenceBundle
    current: PriorTransaction
    should_win: bool
    label_basis: str


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _order(raw: dict[str, Any] | None) -> Order | None:
    if not raw:
        return None
    return Order(
        id=raw["id"],
        payment_id=raw["payment_id"],
        placed_at=_dt(raw["placed_at"]),
        amount=raw["amount"],
        items=tuple(LineItem(**i) for i in raw["items"]),
        billing_address=raw["billing_address"],
        shipping_address=raw["shipping_address"],
        customer_email=raw["customer_email"],
        customer_phone=raw["customer_phone"],
    )


def _delivery(raw: dict[str, Any] | None) -> DeliveryProof | None:
    if not raw:
        return None
    return DeliveryProof(
        tracking_id=raw["tracking_id"],
        delivered_at=_dt(raw["delivered_at"]) if raw.get("delivered_at") else None,
        signed_by=raw.get("signed_by"),
        delivered_to_address=raw.get("delivered_to_address"),
        carrier=raw["carrier"],
        document_uri=raw["document_uri"],
        source_spans=raw.get("source_spans", {}),
    )


def _support(raw: dict[str, Any] | None) -> SupportThread | None:
    if not raw:
        return None
    return SupportThread(
        id=raw["id"],
        messages=tuple(
            SupportMessage(at=_dt(m["at"]), sender=m["sender"], body=m["body"])
            for m in raw["messages"]
        ),
    )


def _policy(raw: dict[str, Any] | None) -> MerchantPolicy | None:
    if not raw:
        return None
    return MerchantPolicy(
        refund_policy_text=raw["refund_policy_text"],
        terms_text=raw["terms_text"],
        effective_from=_dt(raw["effective_from"]),
        document_uri=raw["document_uri"],
    )


def _prior(raw: dict[str, Any]) -> PriorTransaction:
    return PriorTransaction(
        payment_id=raw["payment_id"],
        paid_at=_dt(raw["paid_at"]),
        amount=raw["amount"],
        undisputed=raw["undisputed"],
        device_id=raw.get("device_id"),
        ip_address=raw.get("ip_address"),
        shipping_address=raw.get("shipping_address"),
        account_id=raw.get("account_id"),
    )


def _missing(raw: dict[str, Any]) -> tuple[str, ...]:
    slots = {
        "order": raw.get("order"),
        "delivery_proof": raw.get("delivery"),
        "support_thread": raw.get("support"),
        "device_fingerprint": raw.get("device"),
        "merchant_policy": raw.get("policy"),
    }
    return tuple(name for name, value in slots.items() if not value)


def load_case(path: str | Path) -> Case:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    d = raw["dispute"]

    dispute = Dispute(
        id=d["id"],
        payment_id=d["payment_id"],
        amount=d["amount"],
        currency=d["currency"],
        reason_code=d["reason_code"],
        reason_description=d["reason_description"],
        respond_by=_dt(d["respond_by"]),
        status=d["status"],
        phase=d["phase"],
        created_at=_dt(d["created_at"]),
    )

    bundle = EvidenceBundle(
        dispute_id=dispute.id,
        order=_order(raw.get("order")),
        delivery=_delivery(raw.get("delivery")),
        support=_support(raw.get("support")),
        device=(
            DeviceFingerprint(
                device_id=raw["device"]["device_id"],
                ip_address=raw["device"]["ip_address"],
                user_agent=raw["device"]["user_agent"],
                seen_at=_dt(raw["device"]["seen_at"]),
            )
            if raw.get("device")
            else None
        ),
        prior_transactions=tuple(_prior(p) for p in raw.get("prior_transactions", [])),
        policy=_policy(raw.get("policy")),
        refund_confirmation_uri=raw.get("refund_confirmation_uri"),
        missing=_missing(raw),
    )

    return Case(
        case_id=raw["case_id"],
        dispute=dispute,
        bundle=bundle,
        current=_prior(raw["current_identifiers"]),
        should_win=raw["label"]["should_win"],
        label_basis=raw["label"]["basis"],
    )


def load_split(directory: str | Path) -> list[Case]:
    paths = sorted(p for p in Path(directory).glob("case_*.json"))
    return [load_case(p) for p in paths]
