"""Win-probability model.

A transparent additive model in log-odds space, not a black box. Every feature
has a named coefficient a reviewer can read, and every prediction can be broken
down into per-feature contributions for the merchant console.

There are two possible models at runtime and the eval report always says which
one produced its numbers:

**prior** - the hand-set coefficients below, taken from published representment
win rates (manual filings win 8-20%; structured evidence pushes past 50%). Used
only when no fitted artefact is present.

**fitted** - coefficients learned from `data/train` by `scripts/fit_win_model.py`
and committed to `data/model/win_model.json`, together with a calibration map
chosen by cross-validation. This is what ships.

Calibration matters more than accuracy here, because the EV engine *multiplies*
by this number. A model that is right about ordering but wrong about magnitude
produces confident nonsense in rupees, and the escalation margin in `ev.py` is
derived from this model's measured calibration error.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from vakil.decide.fit import IdentityScaler, Scaler, scaler_from_dict
from vakil.models import CE3Result, Dispute, EvidenceBundle, ReasonCode

DEFAULT_MODEL_PATH = Path("data/model/win_model.json")

#: Evidence features, in the canonical order the fitted coefficients assume.
EVIDENCE_FEATURES = (
    "ce3_qualified",
    "signed_delivery_proof",
    "delivery_proof_unsigned",
    "address_match",
    "device_match",
    "support_thread_present",
    "policy_available",
    "refund_already_issued",
    "evidence_completeness",
)

#: One indicator per dispute condition. Collinear with the intercept by
#: construction, which is why the fitter penalises coefficients but not the
#: intercept.
REASON_FEATURES = tuple(f"rc_{code.value.replace('.', '_')}" for code in ReasonCode)

FEATURE_NAMES = EVIDENCE_FEATURES + REASON_FEATURES

#: Base log-odds per dispute condition with no evidence, used by the prior
#: model. Fraud claims are winnable with the right proof; "not as described" is
#: not, because a cardholder's judgement of quality is hard to rebut on paper.
PRIOR_BASE_LOG_ODDS: dict[ReasonCode, float] = {
    ReasonCode.FRAUD_CARD_ABSENT: -1.60,      # ~17%
    ReasonCode.NON_DELIVERY: -0.85,           # ~30%
    ReasonCode.NOT_AS_DESCRIBED: -2.20,       # ~10%
    ReasonCode.CANCELLED_RECURRING: -1.10,    # ~25%
    ReasonCode.CREDIT_NOT_PROCESSED: -1.95,   # ~12%
    ReasonCode.DUPLICATE_PROCESSING: 0.40,    # ~60%, usually provable from records
}

PRIOR_COEFFICIENTS: dict[str, float] = {
    "ce3_qualified": 2.80,          # the single strongest signal available
    "signed_delivery_proof": 1.30,
    "delivery_proof_unsigned": 0.55,
    "address_match": 0.70,
    "device_match": 0.60,
    "support_thread_present": 0.35,
    "policy_available": 0.30,
    "refund_already_issued": 1.80,  # near-decisive on 13.6 and duplicate claims
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

    def as_dict(self) -> dict[str, float]:
        return {name: float(getattr(self, name)) for name in EVIDENCE_FEATURES}


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


def feature_vector(reason_code: ReasonCode, features: WinFeatures) -> tuple[float, ...]:
    """One row, in `FEATURE_NAMES` order. Shared by fitting and inference so
    the two can never drift apart."""
    values = features.as_dict()
    row = [values[name] for name in EVIDENCE_FEATURES]
    for code in ReasonCode:
        row.append(1.0 if code is reason_code else 0.0)
    return tuple(row)


@dataclass(frozen=True)
class WinModel:
    intercept: float
    coefficients: dict[str, float]
    calibration: Scaler = field(default_factory=IdentityScaler)
    source: str = "prior"
    metadata: dict[str, Any] = field(default_factory=dict)

    def contributions(self, reason_code: ReasonCode, features: WinFeatures) -> dict[str, float]:
        """Per-feature log-odds contribution, so a merchant can see exactly why
        a case scored the way it did. Zero-valued features are dropped - a list
        of things that did not apply is noise, not explanation."""
        row = feature_vector(reason_code, features)
        out: dict[str, float] = {}
        for name, value in zip(FEATURE_NAMES, row, strict=True):
            weight = self.coefficients.get(name, 0.0) * value
            if abs(weight) > 1e-9:
                out[name] = round(weight, 4)
        return out

    def raw_probability(self, reason_code: ReasonCode, features: WinFeatures) -> float:
        """Before calibration. Kept separate so the eval report can show what
        calibration actually changed."""
        row = feature_vector(reason_code, features)
        log_odds = self.intercept + sum(
            self.coefficients.get(name, 0.0) * value
            for name, value in zip(FEATURE_NAMES, row, strict=True)
        )
        return 1.0 / (1.0 + math.exp(-log_odds)) if log_odds > -700 else 0.0

    def probability(self, reason_code: ReasonCode, features: WinFeatures) -> float:
        return self.calibration.apply(self.raw_probability(reason_code, features))

    # ------------------------------------------------------------ persistence

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "intercept": self.intercept,
            "coefficients": self.coefficients,
            "calibration": self.calibration.to_dict(),
            "feature_names": list(FEATURE_NAMES),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> WinModel:
        names = payload.get("feature_names")
        if names is not None and tuple(names) != FEATURE_NAMES:
            raise ValueError(
                "fitted model was trained on a different feature set - refit with "
                "`make fit` rather than loading coefficients that no longer line up"
            )
        return cls(
            intercept=float(payload["intercept"]),
            coefficients={k: float(v) for k, v in payload["coefficients"].items()},
            calibration=scaler_from_dict(payload.get("calibration", {})),
            source=payload.get("source", "fitted"),
            metadata=payload.get("metadata", {}),
        )


def prior_model() -> WinModel:
    """The hand-set starting point, expressed in the same shape as a fitted
    model so the two are interchangeable at every call site."""
    coefficients = dict(PRIOR_COEFFICIENTS)
    for code, log_odds in PRIOR_BASE_LOG_ODDS.items():
        coefficients[f"rc_{code.value.replace('.', '_')}"] = log_odds
    return WinModel(
        intercept=0.0,
        coefficients=coefficients,
        calibration=IdentityScaler(),
        source="prior",
        metadata={"note": "hand-set from published representment win rates; never fitted"},
    )


@lru_cache(maxsize=1)
def active_model(path: str | Path = DEFAULT_MODEL_PATH) -> WinModel:
    """The fitted model if one is committed, otherwise the prior.

    The fallback is deliberate but never silent: `source` travels with the
    model into the ledger and the eval report, so a run that quietly used the
    unfitted prior is visible rather than assumed.
    """
    p = Path(path)
    if not p.exists():
        return prior_model()
    return WinModel.from_dict(json.loads(p.read_text(encoding="utf-8")))


def win_probability(
    dispute: Dispute, features: WinFeatures, model: WinModel | None = None
) -> float:
    return (model or active_model()).probability(dispute.reason_code, features)


def _norm(s: str) -> str:
    return " ".join(s.lower().replace(",", " ").split())
