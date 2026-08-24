"""Fight-or-Fold: the expected-value engine.

Most chargeback tooling assumes "always fight". Fighting is not free - it costs
ops time and gateway fees, and losing a representment can drag a case into
arbitration, which costs more. So the question the merchant actually needs
answered is not *how* to fight but *whether* to.

Two lanes, and keeping them separate matters:

  POST-DISPUTE  A chargeback already exists. The money is already debited, so
                folding nets zero relative to the status quo. Choices are
                FIGHT, FOLD, or ESCALATE (refuse to decide). Network dispute
                ratios are NOT a lever here - the dispute is already counted
                in the numerator whether you contest it or not.

  PRE-DISPUTE   A payment looks likely to be disputed but no chargeback has
                been raised. Refunding now costs the same rupees as losing
                later, but the dispute never enters the ratio at all. This is
                the only lane where VAMP pressure legitimately changes the
                decision, and it is where `PREEMPTIVE_REFUND` lives.

Modelling assumption, stated plainly because the eval report repeats it:
network programs differ on whether a successfully represented dispute is
removed from the ratio numerator. We assume it is not. That is the
conservative reading and it makes the pre-dispute lane's advantage a floor
rather than a claim.

All money is paise.
"""

from __future__ import annotations

from vakil.config import Settings
from vakil.models import CE3Result, Decision, Dispute, EVBreakdown, Verdict

#: Fallback escalation margin, used only when no fitted model is available.
#:
#: The live value is *measured*, not chosen: `scripts/fit_win_model.py` records
#: the fitted model's expected calibration error and stores it as
#: `derived_escalation_margin`, which the pipeline passes in. Read it as "our
#: estimate would have to be wrong by less than its own typical error for this
#: call to flip" - and that typical error is now a number the model reports
#: about itself rather than one somebody picked.
#:
#: The originally chosen 0.08 turned out to be within a thousandth of the
#: measured 0.089, which is reassuring but was luck, not method.
DEFAULT_ESCALATE_BELOW_MARGIN = 0.08

#: Distance from break-even at which the engine is fully confident. Confidence
#: is the margin scaled by this, clamped to 1.0, so the auto-file gate stays a
#: number in [0, 1].
FULL_CONFIDENCE_MARGIN = 0.25


def breakeven_probability(amount: int, cfg: Settings) -> float:
    """The win probability at which fighting exactly breaks even.

    Net EV is `p*A - C - (1-p)*X` for amount A, filing cost C and arbitration
    exposure X. Setting that to zero and solving:

        p* = (C + X) / (A + X)

    Everything the decision depends on collapses into this one number. A large
    dispute has a low break-even (fight on thin odds); a small one has a high
    break-even (only fight when nearly certain). A `p*` above 1.0 means no win
    probability could justify filing - the cost structure has priced the case
    out entirely - and the engine folds without deliberating.
    """
    denominator = amount + cfg.vakil_arbitration_exposure
    if denominator <= 0:
        return float("inf")
    return (cfg.vakil_representment_cost + cfg.vakil_arbitration_exposure) / denominator


def vamp_pressure(ratio: float, threshold: float) -> float:
    """How close is the merchant to the network's dispute-ratio ceiling?

    Returns 0.0 with plenty of headroom, rising to 1.0 at the threshold and
    clamped there. Squared so the penalty stays negligible until the merchant
    is genuinely near trouble, then bites hard.
    """
    if threshold <= 0:
        return 0.0
    return min(max(ratio / threshold, 0.0), 1.0) ** 2


def evaluate(
    dispute: Dispute,
    p_win: float,
    ce3: CE3Result,
    cfg: Settings,
    *,
    exceptions: tuple[str, ...] = (),
    escalation_margin: float | None = None,
) -> Decision:
    """Post-dispute lane. Baseline is FOLD == 0 net.

    `escalation_margin` comes from the fitted model's measured calibration
    error. It is a parameter rather than a constant so that refitting the model
    moves the abstention threshold automatically - a better-calibrated model
    earns the right to decide more cases, and a worse one loses it.
    """
    margin_floor = (
        escalation_margin if escalation_margin is not None else DEFAULT_ESCALATE_BELOW_MARGIN
    )

    gross = round(p_win * dispute.amount)
    arbitration = round((1.0 - p_win) * cfg.vakil_arbitration_exposure)
    net = gross - cfg.vakil_representment_cost - arbitration

    ev = EVBreakdown(
        win_probability=p_win,
        dispute_amount=dispute.amount,
        gross_expected_recovery=gross,
        representment_cost=cfg.vakil_representment_cost,
        arbitration_exposure=arbitration,
        vamp_penalty=0,  # not a lever post-dispute; see module docstring
        net_ev=net,
    )

    p_star = breakeven_probability(dispute.amount, cfg)
    margin = p_win - p_star
    confidence = round(min(abs(margin) / FULL_CONFIDENCE_MARGIN, 1.0), 4)

    if exceptions or abs(margin) < margin_floor:
        return Decision(
            dispute_id=dispute.id,
            verdict=Verdict.ESCALATE,
            ev=ev,
            ce3=ce3,
            confidence=confidence,
            rationale=_escalation_rationale(exceptions, p_win, p_star, margin_floor),
            autofile=False,
            exceptions=exceptions
            or (f"win estimate {p_win:.2f} within {margin_floor:.0%} of break-even "
                f"{p_star:.2f}",),
        )

    if margin > 0:
        verdict = Verdict.FIGHT
        rationale = (
            f"win estimate {p_win:.0%} clears the {p_star:.0%} break-even for a "
            f"{_r(dispute.amount)} dispute by {margin:+.0%}; expected recovery {_r(gross)} "
            f"against {_r(cfg.vakil_representment_cost)} filing cost plus {_r(arbitration)} "
            f"arbitration exposure, net {_r(net)}. {ce3.reason}"
        )
    else:
        verdict = Verdict.FOLD
        rationale = (
            f"win estimate {p_win:.0%} falls {abs(margin):.0%} short of the {p_star:.0%} "
            f"break-even for a {_r(dispute.amount)} dispute; expected recovery {_r(gross)} "
            f"does not cover {_r(cfg.vakil_representment_cost)} filing cost plus "
            f"{_r(arbitration)} arbitration exposure, net {_r(net)}. Contesting would burn "
            f"money on a case that probably loses. {ce3.reason}"
        )

    autofile = (
        verdict is Verdict.FIGHT
        and dispute.amount <= cfg.vakil_autofile_max_amount
        and confidence >= cfg.vakil_autofile_min_confidence
    )

    return Decision(
        dispute_id=dispute.id,
        verdict=verdict,
        ev=ev,
        ce3=ce3,
        confidence=confidence,
        rationale=rationale,
        autofile=autofile,
    )


def evaluate_pre_dispute(
    payment_amount: int,
    p_dispute: float,
    p_win_if_disputed: float,
    cfg: Settings,
) -> tuple[Verdict, EVBreakdown]:
    """Pre-dispute lane: refund now, or let it ride?

    Letting it ride risks the chargeback landing, which costs the amount plus
    filing costs plus a ratio point. Refunding now costs the amount and nothing
    else. VAMP pressure is what tips the balance.
    """
    pressure = vamp_pressure(cfg.vakil_dispute_ratio, cfg.vakil_vamp_threshold)
    penalty = round(pressure * cfg.vakil_vamp_max_penalty)

    # Cost of riding it out, relative to refunding now (which nets -amount).
    expected_recovery_if_fought = round(p_win_if_disputed * payment_amount)
    ride_cost = round(
        p_dispute
        * (payment_amount - expected_recovery_if_fought + cfg.vakil_representment_cost + penalty)
    )
    refund_cost = payment_amount

    ev = EVBreakdown(
        win_probability=p_win_if_disputed,
        dispute_amount=payment_amount,
        gross_expected_recovery=expected_recovery_if_fought,
        representment_cost=cfg.vakil_representment_cost,
        arbitration_exposure=0,
        vamp_penalty=penalty,
        net_ev=refund_cost - ride_cost,
    )
    verdict = Verdict.PREEMPTIVE_REFUND if ride_cost > refund_cost else Verdict.FIGHT
    return verdict, ev


def _escalation_rationale(
    exceptions: tuple[str, ...], p_win: float, p_star: float, margin_floor: float
) -> str:
    if exceptions:
        return "refusing to decide: " + "; ".join(exceptions)
    return (
        f"refusing to decide: win estimate {p_win:.0%} sits within "
        f"{margin_floor:.0%} of the {p_star:.0%} break-even, so an error "
        f"smaller than the model's own would flip the call"
    )


def _r(paise: int) -> str:
    return f"Rs {paise / 100:,.2f}"
