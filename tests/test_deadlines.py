"""Deadline tests. A missed response window is an automatic loss, so the
boundaries here are worth pinning exactly."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from vakil.rules.deadlines import SLATier, deadline_clock

NOW = datetime(2026, 8, 24, 9, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "hours,expected",
    [
        (168, SLATier.NORMAL),
        (49, SLATier.NORMAL),
        (48, SLATier.URGENT),
        (13, SLATier.URGENT),
        (12, SLATier.CRITICAL),
        (0.5, SLATier.CRITICAL),
        (0, SLATier.EXPIRED),
        (-6, SLATier.EXPIRED),
    ],
)
def test_tier_boundaries(hours: float, expected: SLATier):
    sla = deadline_clock(NOW + timedelta(hours=hours), NOW)
    assert sla.tier is expected


def test_expired_is_not_actionable():
    sla = deadline_clock(NOW - timedelta(hours=1), NOW)
    assert not sla.is_actionable
    assert not sla.allows_enrichment


def test_only_normal_tier_allows_enrichment():
    """Under time pressure we skip optional document parsing. A thin filing
    beats a perfect one that arrives after the window closes."""
    assert deadline_clock(NOW + timedelta(hours=72), NOW).allows_enrichment
    assert not deadline_clock(NOW + timedelta(hours=20), NOW).allows_enrichment


def test_hours_left_is_reported():
    sla = deadline_clock(NOW + timedelta(hours=30), NOW)
    assert sla.hours_left == pytest.approx(30.0)
