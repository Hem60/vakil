"""Win-probability model.

A transparent additive scorecard in log-odds space, not a black box. Every
feature has a named coefficient you can defend to a panel, and the whole thing
is refit against `data/train` by `scripts/fit_win_model.py` - the coefficients
below are the starting prior, sourced from published representment win rates
(manual filings win 8-20%; structured evidence pushes past 50%).

Calibration is the metric that matters here, not accuracy. A model that says
70% must win about 70% of the time, because the EV engine multiplies by it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from vakil.models import CE3Result, Dispute, EvidenceBundle, ReasonCode

#: Base log-odds of winning a representment, per reason code, with no evidence.
#: Fraud claims are winnable with the right proof; "not as described" is not,
#: because the customer's subjective judgement is hard to rebut with documents.
BASE_LOG_ODDS: dict[ReasonCode, float] = {
    ReasonCode.FRAUD_CARD_ABSENT: -1.60,      # ~17%
    ReasonCode.NON_DELIVERY: -0.85,           # ~30%
    ReasonCode.NOT_AS_DESCRIBED: -2.20,       # ~10%
    ReasonCode.CANCELLED_RECURRING: -1.10,    # ~25%
    ReasonCode.CREDIT_NOT_PROCESSED: -1.95,   # ~12%
    ReasonCode.DUPLICATE_PROCESSING: 0.40,    # ~60% - usually provable from records
}

COEFFICIENTS = {
    "ce3_qualified": 2.80,          # the single strongest signal available
    "signed_delivery_proof": 1.30,
    "delivery_proof_unsigned": 0.55,
    "address_match": 0.70,
    "device_match": 0.60,
    "support_thread_present": 0.35,
    "policy_available": 0.30,
    "refund_already_issued": 1.80,  # near-decisive on 13.6 / duplicate claims
    "evidence_completeness": 1.10,  # scaled by the completeness fraction
}


@dataclass(frozen=True)
class WinFeatures:
    ce3_qualified: bool
    signed_delivery_proof: bool
    delivery_proof_unsigned: bool
    address_match: bool
    device_match: bool
    support_thread_present: bool
    policy_available: bool
    refund_already_issued: bool
    evidence_completeness: float

    def contributions(self) -> dict[str, float]:
        """Per-feature log-odds contribution. Surfaced in the UI so a merchant
        can see exactly why a case scored the way it did."""
        return {
            "ce3_qualified": COEFFICIENTS["ce3_qualified"] * self.ce3_qualified,
            "signed_delivery_proof": COEFFICIENTS["signed_delivery_proof"]
            * self.signed_delivery_proof,
            "delivery_proof_unsigned": COEFFICIENTS["delivery_proof_unsigned"]
            * self.delivery_proof_unsigned,
            "address_match": COEFFICIENTS["address_match"] * self.address_match,
            "device_match": COEFFICIENTS["device_match"] * self.device_match,
            "support_thread_present": COEFFICIENTS["support_thread_present"]
            * self.support_thread_present,
            "policy_available": COEFFICIENTS["policy_available"] * self.policy_available,
            "refund_already_issued": COEFFICIENTS["refund_already_issued"]
            * self.refund_already_issued,
            "evidence_completeness": COEFFICIENTS["evidence_completeness"]
            * self.evidence_completeness,
        }


def extract_features(bundle: EvidenceBundle, ce3: CE3Result) -> WinFeatures:
    delivery = bundle.delivery
    signed = bool(delivery and delivery.signed_by and delivery.delivered_at)
    unsigned = bool(delivery and delivery.delivered_at and not delivery.signed_by)

    address_match = bool(
        delivery
        and delivery.delivered_to_address
        and bundle.order
        and _norm(delivery.delivered_to_address) == _norm(bundle.order.shipping_address)
    )
    device_match = "device_id" in ce3.matched_identifiers

    return WinFeatures(
        ce3_qualified=ce3.qualifies,
        signed_delivery_proof=signed,
        delivery_proof_unsigned=unsigned,
        address_match=address_match,
        device_match=device_match,
        support_thread_present=bundle.support is not None,
        policy_available=bundle.policy is not None,
        refund_already_issued=bundle.refund_confirmation_uri is not None,
        evidence_completeness=bundle.completeness(),
    )


def win_probability(dispute: Dispute, features: WinFeatures) -> float:
    log_odds = BASE_LOG_ODDS.get(dispute.reason_code, -2.0)
    log_odds += sum(features.contributions().values())
    return 1.0 / (1.0 + math.exp(-log_odds))


def _norm(s: str) -> str:
    return " ".join(s.lower().replace(",", " ").split())
