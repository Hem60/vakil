"""Fight-or-Fold tests.

The EV engine is the only place in Vakil that decides where money goes, so the
tests here are about the *shape* of the decision surface, not point values.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from vakil.config import Settings
from vakil.decide.ev import (
    breakeven_probability,
    evaluate,
    evaluate_pre_dispute,
    vamp_pressure,
)
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


# ---------------------------------------------------------------------------
# Break-even margin: the escalation floor lives in probability space, not
# rupees, so that raising the filing cost moves the threshold instead of
# flooding the human queue. See D7 in docs/DECISIONS.md.
# ---------------------------------------------------------------------------


def test_breakeven_formula():
    """p* = (C + X) / (A + X)."""
    c = cfg()
    p_star = breakeven_probability(500_000, c)
    assert p_star == pytest.approx((25_000 + 80_000) / (500_000 + 80_000))


def test_breakeven_rises_as_the_dispute_shrinks():
    """A small dispute must be nearly certain to be worth filing; a large one
    is worth filing on thin odds. This single number carries the whole
    Fight-or-Fold thesis."""
    c = cfg()
    assert breakeven_probability(5_000_000, c) < breakeven_probability(100_000, c)


def test_breakeven_above_one_means_never_worth_filing():
    """When the cost structure prices a case out entirely, no win probability
    can rescue it - and the engine folds rather than deliberating."""
    c = cfg(vakil_representment_cost=200_000)
    assert breakeven_probability(20_000, c) > 1.0
    d = evaluate(dispute(amount=20_000), p_win=0.99, ce3=CE3_NO, cfg=c)
    assert d.verdict is Verdict.FOLD
    assert d.confidence == 1.0


def test_escalates_only_near_breakeven():
    c = cfg()
    amount = 500_000
    p_star = breakeven_probability(amount, c)

    on_the_line = evaluate(dispute(amount=amount), p_win=p_star + 0.01, ce3=CE3_NO, cfg=c)
    clear_of_it = evaluate(dispute(amount=amount), p_win=p_star + 0.20, ce3=CE3_NO, cfg=c)

    assert on_the_line.verdict is Verdict.ESCALATE
    assert clear_of_it.verdict is Verdict.FIGHT


def test_confidence_is_scale_free():
    """The same distance from break-even means the same confidence whether the
    dispute is Rs 300 or Rs 300,000. The previous floor divided EV by the
    dispute amount, which made confidence collapse as filing costs rose and
    escalated two thirds of the inbox."""
    c = cfg()
    small, large = 30_000, 30_000_000
    a = evaluate(
        dispute(amount=small), p_win=breakeven_probability(small, c) + 0.15, ce3=CE3_NO, cfg=c
    )
    b = evaluate(
        dispute(amount=large), p_win=breakeven_probability(large, c) + 0.15, ce3=CE3_NO, cfg=c
    )
    assert a.confidence == pytest.approx(b.confidence)
    assert a.verdict is b.verdict is Verdict.FIGHT


def test_raising_filing_cost_does_not_flood_escalation():
    """Regression for D7. A case comfortably clear of break-even must keep
    being decided when the filing cost rises - the threshold moves, the
    verdict flips, but the engine does not abstain."""
    amount = 300_000
    cheap = cfg(vakil_representment_cost=25_000)
    dear = cfg(vakil_representment_cost=200_000)

    d_cheap = evaluate(dispute(amount=amount), p_win=0.55, ce3=CE3_NO, cfg=cheap)
    d_dear = evaluate(dispute(amount=amount), p_win=0.55, ce3=CE3_NO, cfg=dear)

    assert d_cheap.verdict is Verdict.FIGHT
    assert d_dear.verdict is Verdict.FOLD
    assert d_dear.verdict is not Verdict.ESCALATE


def test_escalation_rationale_names_the_breakeven():
    """An abstention that does not say what it was unsure about is not an
    exception list, it is a shrug."""
    c = cfg()
    amount = 500_000
    d = evaluate(
        dispute(amount=amount),
        p_win=breakeven_probability(amount, c) + 0.01,
        ce3=CE3_NO,
        cfg=c,
    )
    assert "break-even" in d.rationale
    assert d.exceptions and "break-even" in d.exceptions[0]
