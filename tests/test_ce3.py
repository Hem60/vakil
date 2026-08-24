"""CE 3.0 qualifier tests.

This module decides whether the strongest argument available to a merchant is
on the table, so every rejection branch gets a test that pins the *reason*, not
just the boolean. If Visa changes a threshold, these tests should fail loudly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from vakil.models import Dispute, EvidenceBundle, PriorTransaction, ReasonCode
from vakil.rules.ce3 import qualifies_ce3

DISPUTED_AT = datetime(2026, 8, 20, tzinfo=UTC)
SHIP = "12 MG Road, Bengaluru 560001"


def prior(days_ago: int, *, undisputed: bool = True, **overrides: object) -> PriorTransaction:
    base = {
        "payment_id": f"pay_{days_ago}",
        "paid_at": DISPUTED_AT - timedelta(days=days_ago),
        "amount": 499900,
        "undisputed": undisputed,
        "device_id": "dev_aaa",
        "ip_address": "49.1.2.3",
        "shipping_address": SHIP,
        "account_id": "acct_1",
    }
    base.update(overrides)
    return PriorTransaction(**base)  # type: ignore[arg-type]


CURRENT = prior(0, undisputed=False)


def dispute(reason: ReasonCode = ReasonCode.FRAUD_CARD_ABSENT) -> Dispute:
    return Dispute(
        id="disp_1",
        payment_id="pay_1",
        amount=499900,
        reason_code=reason,
        respond_by=DISPUTED_AT + timedelta(days=7),
        created_at=DISPUTED_AT,
    )


def bundle(*priors: PriorTransaction) -> EvidenceBundle:
    return EvidenceBundle(dispute_id="disp_1", prior_transactions=priors)


def test_qualifies_with_two_aged_matching_priors():
    result = qualifies_ce3(dispute(), bundle(prior(150), prior(200)), CURRENT)
    assert result.qualifies
    assert set(result.matched_identifiers) >= {"device_id", "shipping_address"}
    assert result.citation


def test_rejects_wrong_reason_code():
    """CE 3.0 is a 10.4 remedy. Applying it elsewhere would be a rule error
    that an issuer would reject, so we refuse before spending anything."""
    result = qualifies_ce3(dispute(ReasonCode.NON_DELIVERY), bundle(prior(150), prior(200)), CURRENT)
    assert not result.qualifies
    assert "10.4" in result.reason


@pytest.mark.parametrize("age", [40, 90, 119])
def test_rejects_priors_that_are_too_recent(age: int):
    result = qualifies_ce3(dispute(), bundle(prior(age), prior(age + 5)), CURRENT)
    assert not result.qualifies
    assert "120-365" in result.reason


def test_rejects_priors_that_are_too_old():
    result = qualifies_ce3(dispute(), bundle(prior(400), prior(500)), CURRENT)
    assert not result.qualifies


def test_disputed_priors_do_not_count():
    result = qualifies_ce3(
        dispute(), bundle(prior(150), prior(200, undisputed=False)), CURRENT
    )
    assert not result.qualifies
    assert "found 1" in result.reason


def test_requires_two_matching_identifiers():
    """One shared identifier is a coincidence, not a relationship."""
    priors = (
        prior(150, ip_address="1.1.1.1", shipping_address="elsewhere", account_id="acct_x"),
        prior(200, ip_address="2.2.2.2", shipping_address="elsewhere", account_id="acct_y"),
    )
    result = qualifies_ce3(dispute(), bundle(*priors), CURRENT)
    assert not result.qualifies
    assert result.matched_identifiers == ("device_id",)


def test_missing_identifier_never_matches():
    """A None on both sides is absence of evidence, and must not be scored as
    agreement - that would manufacture a qualification out of missing data."""
    current = prior(0, undisputed=False, device_id=None, ip_address=None, account_id=None)
    priors = (
        prior(150, device_id=None, ip_address=None, account_id=None),
        prior(200, device_id=None, ip_address=None, account_id=None),
    )
    result = qualifies_ce3(dispute(), bundle(*priors), current)
    assert not result.qualifies
    assert result.matched_identifiers == ("shipping_address",)


def test_uses_the_two_oldest_eligible_priors():
    priors = (prior(130), prior(150), prior(300, device_id="dev_other"))
    result = qualifies_ce3(dispute(), bundle(*priors), CURRENT)
    assert result.qualifies
