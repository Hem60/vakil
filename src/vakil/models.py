"""Domain models.

Money is always an integer count of paise. Never a float, anywhere, ever.
Timestamps are timezone-aware UTC datetimes at the boundary; the Razorpay API
speaks Unix seconds and we convert on the way in and out.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --------------------------------------------------------------------------
# Razorpay dispute entity (mirrors razorpay.com/docs/api/disputes/entity)
# --------------------------------------------------------------------------


class DisputeStatus(StrEnum):
    OPEN = "open"
    UNDER_REVIEW = "under_review"
    WON = "won"
    LOST = "lost"
    CLOSED = "closed"


class DisputePhase(StrEnum):
    FRAUD = "fraud"
    RETRIEVAL = "retrieval"
    CHARGEBACK = "chargeback"
    PRE_ARBITRATION = "pre_arbitration"
    ARBITRATION = "arbitration"


class ReasonCode(StrEnum):
    """The subset we handle. Anything else routes to the exception list."""

    FRAUD_CARD_ABSENT = "10.4"        # Visa: other fraud, card-absent environment
    NON_DELIVERY = "13.1"             # Visa: merchandise/services not received
    NOT_AS_DESCRIBED = "13.3"         # Visa: not as described or defective
    CANCELLED_RECURRING = "13.2"      # Visa: cancelled recurring transaction
    CREDIT_NOT_PROCESSED = "13.6"     # Visa: credit not processed
    DUPLICATE_PROCESSING = "12.6"     # Visa: duplicate processing


class Dispute(Frozen):
    id: str
    payment_id: str
    amount: int = Field(description="paise")
    currency: str = "INR"
    reason_code: ReasonCode
    reason_description: str = ""
    respond_by: datetime
    status: DisputeStatus = DisputeStatus.OPEN
    phase: DisputePhase = DisputePhase.CHARGEBACK
    created_at: datetime


# --------------------------------------------------------------------------
# Merchant-side facts, gathered by the evidence harvest
# --------------------------------------------------------------------------


class LineItem(Frozen):
    sku: str
    title: str
    qty: int
    unit_price: int


class Order(Frozen):
    id: str
    payment_id: str
    placed_at: datetime
    amount: int
    items: tuple[LineItem, ...]
    billing_address: str
    shipping_address: str
    customer_email: str
    customer_phone: str


class DeliveryProof(Frozen):
    """Extracted from a courier POD. `source` records where each value came
    from so the provenance gate can cite it."""

    tracking_id: str
    delivered_at: datetime | None
    signed_by: str | None
    delivered_to_address: str | None
    carrier: str
    document_uri: str
    source_spans: dict[str, str] = Field(default_factory=dict)


class SupportMessage(Frozen):
    at: datetime
    sender: str  # "customer" | "merchant"
    body: str


class SupportThread(Frozen):
    id: str
    messages: tuple[SupportMessage, ...]


class DeviceFingerprint(Frozen):
    device_id: str
    ip_address: str
    user_agent: str
    seen_at: datetime


class PriorTransaction(Frozen):
    """A previous payment by (apparently) the same customer. The CE 3.0
    qualifier reads only these fields, so keep them exact."""

    payment_id: str
    paid_at: datetime
    amount: int
    undisputed: bool
    device_id: str | None
    ip_address: str | None
    shipping_address: str | None
    account_id: str | None


class MerchantPolicy(Frozen):
    refund_policy_text: str
    terms_text: str
    effective_from: datetime
    document_uri: str


class EvidenceBundle(Frozen):
    """Everything the harvest could find. Any field may be None - incomplete
    bundles are the normal case, not an error."""

    dispute_id: str
    order: Order | None = None
    delivery: DeliveryProof | None = None
    support: SupportThread | None = None
    device: DeviceFingerprint | None = None
    prior_transactions: tuple[PriorTransaction, ...] = ()
    policy: MerchantPolicy | None = None
    refund_confirmation_uri: str | None = None
    missing: tuple[str, ...] = ()

    def completeness(self) -> float:
        slots = [self.order, self.delivery, self.support, self.device, self.policy]
        return sum(s is not None for s in slots) / len(slots)


# --------------------------------------------------------------------------
# Decisions
# --------------------------------------------------------------------------


class Verdict(StrEnum):
    FIGHT = "FIGHT"
    FOLD = "FOLD"
    PREEMPTIVE_REFUND = "PREEMPTIVE_REFUND"
    ESCALATE = "ESCALATE"  # refuses to decide -> honest exception list


class CE3Result(Frozen):
    qualifies: bool
    reason: str
    matched_identifiers: tuple[str, ...] = ()
    citation: str | None = None


class EVBreakdown(Frozen):
    """Every term kept separate so the UI and the pitch can show the arithmetic."""

    win_probability: float
    dispute_amount: int
    gross_expected_recovery: int
    representment_cost: int
    arbitration_exposure: int
    vamp_penalty: int
    net_ev: int


class Decision(Frozen):
    dispute_id: str
    verdict: Verdict
    ev: EVBreakdown
    ce3: CE3Result
    confidence: float
    rationale: str
    autofile: bool
    exceptions: tuple[str, ...] = ()


# --------------------------------------------------------------------------
# Drafting + provenance
# --------------------------------------------------------------------------


class ClaimStatus(StrEnum):
    VERIFIED = "VERIFIED"
    STRIPPED = "STRIPPED"


class Claim(Frozen):
    text: str
    cited_source: str | None
    cited_span: str | None
    status: ClaimStatus
    note: str = ""


class RebuttalLetter(Frozen):
    dispute_id: str
    body: str
    claims: tuple[Claim, ...]

    @property
    def stripped(self) -> tuple[Claim, ...]:
        return tuple(c for c in self.claims if c.status is ClaimStatus.STRIPPED)
