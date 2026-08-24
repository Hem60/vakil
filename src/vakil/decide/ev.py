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

#: Below this confidence the engine refuses to decide and hands the case to a
#: human. Those refusals become the honest exception list in the eval report.
ESCALATE_BELOW_CONFIDENCE = 0.35

#: A case whose EV straddles zero by less than this is not a real signal.
EV_INDIFFERENCE_BAND = 5_000


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
) -> Decision:
    """Post-dispute lane. Baseline is FOLD == 0 net."""

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

    confidence = _confidence(p_win, net, dispute.amount)

    if exceptions or confidence < ESCALATE_BELOW_CONFIDENCE:
        return Decision(
            dispute_id=dispute.id,
            verdict=Verdict.ESCALATE,
            ev=ev,
            ce3=ce3,
            confidence=confidence,
            rationale=_escalation_rationale(exceptions, confidence),
            autofile=False,
            exceptions=exceptions or ("low decision confidence",),
        )

    if net > EV_INDIFFERENCE_BAND:
        verdict = Verdict.FIGHT
        rationale = (
            f"expected recovery {_r(gross)} exceeds {_r(cfg.vakil_representment_cost)} "
            f"filing cost plus {_r(arbitration)} arbitration exposure; net {_r(net)}. "
            f"{ce3.reason}"
        )
    else:
        verdict = Verdict.FOLD
        rationale = (
            f"expected recovery {_r(gross)} does not cover {_r(cfg.vakil_representment_cost)} "
            f"filing cost plus {_r(arbitration)} arbitration exposure; net {_r(net)}. "
            f"Contesting would burn money on a case that probably loses. {ce3.reason}"
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


def _confidence(p_win: float, net_ev: int, amount: int) -> float:
    """How sure are we of the *verdict* - which is not how likely we are to win.

    Deliberately a function of EV margin alone. An earlier version also rewarded
    p_win for being far from 0.5, which was wrong: a well-calibrated 0.45 is a
    precisely known quantity, not an uncertain one, and penalising it escalated
    cases whose arithmetic was in fact unambiguous. What makes a verdict shaky
    is EV sitting near zero relative to the money on the table.
    """
    if amount <= 0:
        return 0.0
    return round(min(abs(net_ev) / amount, 1.0), 4)


def _escalation_rationale(exceptions: tuple[str, ...], confidence: float) -> str:
    if exceptions:
        return "refusing to decide: " + "; ".join(exceptions)
    return f"refusing to decide: verdict confidence {confidence:.2f} below floor"


def _r(paise: int) -> str:
    return f"Rs {paise / 100:,.2f}"
