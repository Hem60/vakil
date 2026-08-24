"""Deadline arithmetic. Pure functions - no clock reads inside, the caller
passes `now`, which is what makes the whole pipeline replayable."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum

from vakil.models import Frozen


class SLATier(StrEnum):
    NORMAL = "NORMAL"        # >48h: full pipeline, VLM parsing, everything
    URGENT = "URGENT"        # 12-48h: skip optional enrichment
    CRITICAL = "CRITICAL"    # <12h: file a reduced pack immediately
    EXPIRED = "EXPIRED"      # deadline passed: automatic loss, do not spend money


class SLA(Frozen):
    remaining: timedelta
    tier: SLATier
    hours_left: float

    @property
    def allows_enrichment(self) -> bool:
        return self.tier is SLATier.NORMAL

    @property
    def is_actionable(self) -> bool:
        return self.tier is not SLATier.EXPIRED


URGENT_AFTER_HOURS = 48.0
CRITICAL_AFTER_HOURS = 12.0


def deadline_clock(respond_by: datetime, now: datetime) -> SLA:
    """Classify how much runway a dispute has left.

    A missed deadline is an automatic loss, so EXPIRED short-circuits the
    pipeline before any model call is paid for.
    """
    remaining = respond_by - now
    hours = remaining.total_seconds() / 3600.0

    # Boundaries are inclusive on the tighter side: "48 hours left" is URGENT,
    # not NORMAL. When a deadline is a hard loss, round toward caution.
    if hours <= 0:
        tier = SLATier.EXPIRED
    elif hours <= CRITICAL_AFTER_HOURS:
        tier = SLATier.CRITICAL
    elif hours <= URGENT_AFTER_HOURS:
        tier = SLATier.URGENT
    else:
        tier = SLATier.NORMAL

    return SLA(remaining=remaining, tier=tier, hours_left=hours)
