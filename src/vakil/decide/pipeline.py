"""The decision path, end to end, with no I/O and no model calls.

Everything here is a pure function of (case, config, now). That is what makes
the eval harness meaningful and `ledger.replay()` exact: given the same inputs
you get the same decision, today or in six months.

Stages 3 (document extraction) and 6 (drafting) are the model-bearing parts of
Vakil and they live elsewhere. Nothing in this module can hallucinate.
"""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache

from vakil.config import Settings
from vakil.decide.ev import evaluate
from vakil.decide.win import WinFeatures, extract_features, win_probability
from vakil.ingest.corpus import Case
from vakil.models import CE3Result, Decision, Verdict
from vakil.rulebook.store import EvidenceGap, Rule, Rulebook, blocking_gaps, evidence_gaps
from vakil.rules.ce3 import qualifies_ce3
from vakil.rules.deadlines import SLA, deadline_clock

#: Evidence slots whose absence we refuse to paper over.
CRITICAL_SLOTS = ("order",)


def collect_exceptions(case: Case, sla: SLA) -> tuple[str, ...]:
    """Reasons to hand this case to a human instead of deciding it.

    These become the honest exception list in the eval report. A system that
    never refuses is not confident, it is untested.
    """
    out: list[str] = []
    if not sla.is_actionable:
        out.append(f"response deadline passed {abs(sla.hours_left):.1f}h ago")
    for slot in CRITICAL_SLOTS:
        if slot in case.bundle.missing:
            out.append(f"missing {slot}, cannot establish the transaction")
    if case.bundle.completeness() < 0.25:
        out.append(f"evidence {case.bundle.completeness():.0%} complete, too thin to argue")
    return tuple(out)


class Assessment:
    """Decision plus the working that produced it, for the UI and the ledger."""

    def __init__(
        self,
        case: Case,
        sla: SLA,
        ce3: CE3Result,
        features: WinFeatures,
        p_win: float,
        decision: Decision,
        requirements: list[Rule],
        gaps: list[EvidenceGap],
    ) -> None:
        self.case = case
        self.sla = sla
        self.ce3 = ce3
        self.features = features
        self.p_win = p_win
        self.decision = decision
        self.requirements = requirements
        self.gaps = gaps

    @property
    def verdict(self) -> Verdict:
        return self.decision.verdict

    @property
    def blocking(self) -> list[EvidenceGap]:
        return blocking_gaps(self.gaps)

    def to_ledger_payload(self) -> dict:
        return {
            "case_id": self.case.case_id,
            "reason_code": str(self.case.dispute.reason_code),
            "sla_tier": str(self.sla.tier),
            "hours_left": round(self.sla.hours_left, 2),
            "ce3": self.ce3.model_dump(),
            "features": self.features.__dict__,
            "feature_contributions": self.features.contributions(),
            "p_win": self.p_win,
            "decision": self.decision.model_dump(),
            "requirements": [
                {"rule_id": r.id, "necessity": str(r.necessity), "citation": r.citation.render()}
                for r in self.requirements
            ],
            "evidence_gaps": [g.model_dump() for g in self.gaps],
        }


def assess(
    case: Case,
    cfg: Settings,
    now: datetime,
    rulebook: Rulebook | None = None,
) -> Assessment:
    """Decide one case.

    Still a pure function of its arguments - the rulebook is passed in rather
    than loaded, so the eval harness and `replay` stay exact.

    **Evidence gaps inform; they do not gate.** A missing required document
    lowers the win probability, and the EV engine folds on its own if the case
    cannot be argued. Escalating every case with a gap would flood the human
    queue with cases a human cannot fix either - the exact failure D7 removed.
    Gaps are surfaced to the drafting stage, which must not claim what is not
    held, and to the merchant console, which can go and look for the document.
    """
    sla = deadline_clock(case.dispute.respond_by, now)
    ce3 = qualifies_ce3(case.dispute, case.bundle, case.current)
    features = extract_features(case.bundle, ce3)
    p_win = win_probability(case.dispute, features)
    exceptions = collect_exceptions(case, sla)
    decision = evaluate(case.dispute, p_win, ce3, cfg, exceptions=exceptions)

    book = rulebook if rulebook is not None else default_rulebook()
    requirements = book.requirements_for(case.dispute.reason_code)
    gaps = evidence_gaps(requirements, case.bundle, ce3_qualifies=ce3.qualifies)

    return Assessment(case, sla, ce3, features, p_win, decision, requirements, gaps)


@lru_cache(maxsize=1)
def default_rulebook() -> Rulebook:
    """Loaded once. The corpus is static at runtime; re-reading twenty JSON
    entries per dispute would be waste, not caution."""
    return Rulebook.load()
