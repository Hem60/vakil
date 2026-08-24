"""Fight-or-Fold tests.

The EV engine is the only place in Vakil that decides where money goes, so the
tests here are about the *shape* of the decision surface, not point values.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from vakil.config import Settings
from vakil.decide.ev import evaluate, evaluate_pre_dispute, vamp_pressure
from vakil.models import CE3Result, Dispute, ReasonCode, Verdict

NOW = datetime(2026, 8, 24, tzinfo=UTC)
CE3_NO = CE3Result(qualifies=False, reason="no priors")


def cfg(**overrides: object) -> Settings:
    base = Settings(
        vakil_representment_cost=25_000,
        vakil_arbitration_exposure=80_000,
        vakil_dispute_ratio=0.004,
        vakil_vamp_threshold=0.009,
    )
    return base.model_copy(update=overrides)


def dispute(amount: int = 499900) -> Dispute:
    return Dispute(
        id="disp_1",
        payment_id="pay_1",
        amount=amount,
        reason_code=ReasonCode.FRAUD_CARD_ABSENT,
        respond_by=NOW + timedelta(days=7),
        created_at=NOW,
    )


def test_high_win_probability_fights():
    d = evaluate(dispute(), p_win=0.92, ce3=CE3_NO, cfg=cfg())
    assert d.verdict is Verdict.FIGHT
    assert d.ev.net_ev > 0


def test_low_win_probability_on_a_small_dispute_folds():
    """The whole thesis: when the recoverable amount cannot cover the cost of
    recovering it, filing is a way to lose money more slowly."""
    d = evaluate(dispute(amount=30_000), p_win=0.10, ce3=CE3_NO, cfg=cfg())
    assert d.verdict is Verdict.FOLD
    assert d.ev.net_ev < 0


def test_same_probability_opposite_verdicts_by_amount():
    """Two cases, identical odds, opposite calls - this is the demo."""
    small = evaluate(dispute(amount=28_000), p_win=0.45, ce3=CE3_NO, cfg=cfg())
    large = evaluate(dispute(amount=900_000), p_win=0.45, ce3=CE3_NO, cfg=cfg())
    assert small.verdict is Verdict.FOLD
    assert large.verdict is Verdict.FIGHT


def test_exceptions_force_escalation_regardless_of_ev():
    d = evaluate(
        dispute(), p_win=0.95, ce3=CE3_NO, cfg=cfg(), exceptions=("missing order",)
    )
    assert d.verdict is Verdict.ESCALATE
    assert not d.autofile
    assert "missing order" in d.rationale


def test_autofile_gated_on_amount():
    """High-value disputes get a human even when the maths is obvious."""
    small = evaluate(dispute(amount=500_000), p_win=0.95, ce3=CE3_NO, cfg=cfg())
    large = evaluate(dispute(amount=5_000_000), p_win=0.95, ce3=CE3_NO, cfg=cfg())
    assert small.autofile
    assert large.verdict is Verdict.FIGHT and not large.autofile


def test_ev_breakdown_terms_reconcile():
    d = evaluate(dispute(), p_win=0.60, ce3=CE3_NO, cfg=cfg())
    ev = d.ev
    assert ev.net_ev == (
        ev.gross_expected_recovery - ev.representment_cost - ev.arbitration_exposure
    )


def test_vamp_pressure_is_zero_with_headroom_and_saturates():
    assert vamp_pressure(0.0, 0.009) == 0.0
    assert vamp_pressure(0.02, 0.009) == 1.0
    assert vamp_pressure(0.004, 0.009) < vamp_pressure(0.008, 0.009)


def test_vamp_pressure_handles_zero_threshold():
    assert vamp_pressure(0.005, 0.0) == 0.0


@pytest.mark.parametrize("ratio,expect", [(0.0001, Verdict.FIGHT), (0.0089, Verdict.PREEMPTIVE_REFUND)])
def test_pre_dispute_lane_flips_under_vamp_pressure(ratio: float, expect: Verdict):
    """Same payment, same odds - only the merchant's dispute ratio differs.
    With headroom, ride it out. Near the ceiling, refund before it lands."""
    verdict, _ = evaluate_pre_dispute(
        payment_amount=200_000,
        p_dispute=0.80,
        p_win_if_disputed=0.30,
        cfg=cfg(vakil_dispute_ratio=ratio, vakil_vamp_max_penalty=400_000),
    )
    assert verdict is expect
