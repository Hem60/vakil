"""Visa Compelling Evidence 3.0 qualifier.

CE 3.0 lets a merchant rebut a card-absent fraud claim (Visa reason code 10.4)
by proving a prior relationship with the cardholder: two earlier undisputed
transactions that share identifiers with the disputed one. When it qualifies,
the issuer is obliged to accept the evidence, so win probability jumps sharply.

This module contains NO model calls and NO heuristics. Every branch is a
rulebook clause, and every rejection names the clause it failed. That is the
point: the part of the system that moves money is code you can read.

Thresholds below are the rulebook's, not ours. If Visa changes them, change
them here and the eval suite will tell you what moved.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from vakil.models import CE3Result, Dispute, EvidenceBundle, PriorTransaction, ReasonCode

CITATION = "Visa VCR - Compelling Evidence 3.0 (dispute condition 10.4)"

#: A qualifying prior transaction must be at least this old on the dispute date.
MIN_AGE = timedelta(days=120)
#: ...and no older than this.
MAX_AGE = timedelta(days=365)
#: Number of qualifying prior transactions required.
REQUIRED_TRANSACTIONS = 2
#: Number of identifiers that must match across them.
REQUIRED_MATCHERS = 2

MATCHER_FIELDS = ("device_id", "ip_address", "shipping_address", "account_id")


def _eligible(priors: tuple[PriorTransaction, ...], anchor: datetime) -> list[PriorTransaction]:
    out = []
    for t in priors:
        if not t.undisputed:
            continue
        age = anchor - t.paid_at
        if MIN_AGE <= age <= MAX_AGE:
            out.append(t)
    return sorted(out, key=lambda t: t.paid_at)


def _matched_identifiers(
    candidates: list[PriorTransaction], current: PriorTransaction
) -> tuple[str, ...]:
    """An identifier counts only if it is present on the current transaction
    AND identical on every candidate. A missing value never matches."""
    matched = []
    for field in MATCHER_FIELDS:
        cur = getattr(current, field)
        if cur is None:
            continue
        if all(getattr(t, field) == cur for t in candidates):
            matched.append(field)
    return tuple(matched)


def qualifies_ce3(
    dispute: Dispute,
    bundle: EvidenceBundle,
    current: PriorTransaction,
) -> CE3Result:
    """Decide whether this dispute qualifies for CE 3.0 rebuttal."""

    if dispute.reason_code is not ReasonCode.FRAUD_CARD_ABSENT:
        return CE3Result(
            qualifies=False,
            reason=f"CE 3.0 applies only to reason code 10.4, this is {dispute.reason_code}",
        )

    eligible = _eligible(bundle.prior_transactions, anchor=dispute.created_at)
    if len(eligible) < REQUIRED_TRANSACTIONS:
        return CE3Result(
            qualifies=False,
            reason=(
                f"needs {REQUIRED_TRANSACTIONS} prior undisputed transactions aged "
                f"{MIN_AGE.days}-{MAX_AGE.days} days, found {len(eligible)}"
            ),
            citation=CITATION,
        )

    candidates = eligible[:REQUIRED_TRANSACTIONS]
    matched = _matched_identifiers(candidates, current)
    if len(matched) < REQUIRED_MATCHERS:
        return CE3Result(
            qualifies=False,
            reason=(
                f"needs {REQUIRED_MATCHERS} matching identifiers across priors, "
                f"matched {len(matched)}: {matched or 'none'}"
            ),
            matched_identifiers=matched,
            citation=CITATION,
        )

    return CE3Result(
        qualifies=True,
        reason=(
            f"{len(candidates)} prior undisputed transactions share "
            f"{len(matched)} identifiers: {', '.join(matched)}"
        ),
        matched_identifiers=matched,
        citation=CITATION,
    )
