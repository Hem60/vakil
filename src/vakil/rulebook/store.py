"""The rulebook: what each dispute condition requires, and where that comes from.

**Reason code to requirements is a lookup, not a retrieval.** Vector search
over a rulebook would put a model between a dispute and the evidence the
network demands for it - a place where being approximately right is being
wrong, and where a table is exact, auditable, and free. Semantic search exists
here too (see `search.py`), but it serves the drafting stage looking for
supporting context, never the requirements mapping.

A licensing note that shapes this whole module: Visa Core Rules, the VCR
dispute-condition guide and the Mastercard chargeback guide are **proprietary
documents**. They are not ingested, embedded, or reproduced. Every entry in
`data/rulebook/` is a short summary written for this project, carrying a
citation to the rule it summarises so a licensed copy can be checked against
it. Entries are marked `verified=false` until someone has done that check
against a licensed rulebook; `verified=true` means the claim was taken from
public documentation, mostly Razorpay's.

That is a real limitation and it is surfaced, not hidden: `Rulebook.coverage()`
reports how much of the corpus is unverified, and the drafting stage is expected
to refuse to cite an unverified rule as though it were settled law.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from vakil.models import EvidenceBundle, ReasonCode

DEFAULT_RULEBOOK_DIR = Path("data/rulebook")


class Necessity(StrEnum):
    #: Without this, the representment is very unlikely to succeed.
    REQUIRED = "required"
    #: Satisfies the condition on its own, in place of the required set.
    ALTERNATIVE = "alternative"
    #: Strengthens the case but does not carry it.
    SUPPORTING = "supporting"
    #: Background that shapes strategy; not evidence to file.
    CONTEXT = "context"


class Citation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    document: str
    section: str
    url: str

    def render(self) -> str:
        return f"{self.document} - {self.section}"


class Rule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    #: None means the rule applies to every dispute condition.
    reason_code: ReasonCode | None = None
    title: str
    requirement: str
    evidence_fields: tuple[str, ...] = ()
    necessity: Necessity
    #: Rules sharing a group are alternatives to one another - satisfying any
    #: one satisfies the group. Physical delivery proof and digital service
    #: logs are both "required" for non-delivery disputes, but an order can
    #: only ever produce one of them, so demanding both would manufacture a gap
    #: that no merchant could ever close.
    group: str | None = None
    note: str = ""
    citation: Citation
    #: False until checked against a licensed rulebook. See module docstring.
    verified: bool = False

    @property
    def is_universal(self) -> bool:
        return self.reason_code is None

    @property
    def is_filable(self) -> bool:
        """Does this rule call for a document, or is it background?"""
        return self.necessity is not Necessity.CONTEXT and bool(self.evidence_fields)

    def render(self) -> str:
        mark = "" if self.verified else " [unverified]"
        return f"{self.title} ({self.citation.render()}){mark}: {self.requirement}"


class RulebookError(RuntimeError):
    pass


#: Which Razorpay evidence field each slot of a harvested bundle can satisfy.
#: This is the bridge the whole module exists to provide: a network requirement
#: on one side, a Razorpay Documents API field on the other.
BUNDLE_TO_EVIDENCE_FIELD: dict[str, tuple[str, ...]] = {
    "delivery": ("shipping_proof",),
    "order": ("billing_proof",),
    "support": ("customer_communication",),
    "policy": ("refund_cancellation_policy", "term_and_conditions"),
    "refund_confirmation_uri": ("refund_confirmation",),
    "device": ("access_activity_log", "proof_of_service"),
    "prior_transactions": ("others",),
}

#: Fields Vakil produces itself rather than harvesting.
GENERATED_EVIDENCE_FIELDS = frozenset({"explanation_letter"})


class Rulebook:
    def __init__(self, rules: list[Rule]) -> None:
        seen: set[str] = set()
        for rule in rules:
            if rule.id in seen:
                raise RulebookError(f"duplicate rule id: {rule.id}")
            seen.add(rule.id)
        self._rules = rules
        self._by_reason: dict[ReasonCode | None, list[Rule]] = {}
        for rule in rules:
            self._by_reason.setdefault(rule.reason_code, []).append(rule)

    # ------------------------------------------------------------- loading

    @classmethod
    def load(cls, directory: str | Path = DEFAULT_RULEBOOK_DIR) -> Rulebook:
        path = Path(directory)
        files = sorted(path.glob("*.json"))
        if not files:
            raise RulebookError(f"no rulebook files in {path}")

        rules: list[Rule] = []
        for file in files:
            payload = json.loads(file.read_text(encoding="utf-8"))
            for raw in payload["rules"]:
                rules.append(Rule.model_validate(raw))
        return cls(rules)

    def __len__(self) -> int:
        return len(self._rules)

    def __iter__(self) -> Iterator[Rule]:
        return iter(self._rules)

    # ---------------------------------------------------------- retrieval

    def requirements_for(self, reason_code: ReasonCode) -> list[Rule]:
        """Every rule bearing on this dispute condition, specific first.

        Deterministic. Same input, same rules, same citations, every time.
        """
        specific = self._by_reason.get(reason_code, [])
        universal = self._by_reason.get(None, [])
        order = {
            Necessity.REQUIRED: 0,
            Necessity.ALTERNATIVE: 1,
            Necessity.SUPPORTING: 2,
            Necessity.CONTEXT: 3,
        }
        return sorted(specific + universal, key=lambda r: (order[r.necessity], r.id))

    def by_id(self, rule_id: str) -> Rule:
        for rule in self._rules:
            if rule.id == rule_id:
                return rule
        raise RulebookError(f"no such rule: {rule_id}")

    # ----------------------------------------------------------- coverage

    def coverage(self) -> dict[str, Any]:
        """How much of this corpus has actually been checked against a licensed
        rulebook. Reported so the number is visible rather than assumed."""
        verified = sum(1 for r in self._rules if r.verified)
        codes = {r.reason_code for r in self._rules if r.reason_code}
        return {
            "rules": len(self._rules),
            "verified": verified,
            "unverified": len(self._rules) - verified,
            "verified_fraction": round(verified / len(self._rules), 4) if self._rules else 0.0,
            "reason_codes_covered": sorted(str(c) for c in codes),
            "reason_codes_missing": sorted(
                str(c) for c in ReasonCode if c not in codes
            ),
        }


class EvidenceGap(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str
    title: str
    necessity: Necessity
    missing_fields: tuple[str, ...]
    citation: Citation
    #: True when another rule marked `alternative` is satisfied instead.
    excused_by: str | None = None


def satisfied_evidence_fields(bundle: EvidenceBundle) -> set[str]:
    """Which Razorpay evidence fields this bundle could actually populate."""
    satisfied: set[str] = set()
    for slot, fields in BUNDLE_TO_EVIDENCE_FIELD.items():
        value = getattr(bundle, slot, None)
        if value:
            satisfied.update(fields)
    return satisfied


def evidence_gaps(
    rules: list[Rule],
    bundle: EvidenceBundle,
    *,
    ce3_qualifies: bool = False,
) -> list[EvidenceGap]:
    """What the network wants for this dispute that the merchant does not have.

    Feeds three things: the drafting stage (do not claim what is not held), the
    exception list (refuse cases missing something required), and the merchant
    console (go and find this document).

    An `alternative` rule that is satisfied excuses the required set - CE 3.0 is
    exactly that case, which is why it needs to be passed in rather than
    re-derived here.
    """
    have = satisfied_evidence_fields(bundle) | GENERATED_EVIDENCE_FIELDS
    excuse = "visa-ce3-prior-undisputed" if ce3_qualifies else None
    filable = [r for r in rules if r.is_filable and r.necessity is not Necessity.SUPPORTING]

    def is_satisfied(rule: Rule) -> bool:
        return all(field in have for field in rule.evidence_fields)

    # A group is satisfied as soon as any member is, so alternatives inside it
    # stop being reported as gaps.
    satisfied_groups = {r.group for r in filable if r.group and is_satisfied(r)}

    gaps: list[EvidenceGap] = []
    for rule in filable:
        if rule.id == excuse:
            continue
        if rule.group and rule.group in satisfied_groups:
            continue
        missing = tuple(f for f in rule.evidence_fields if f not in have)
        if not missing:
            continue
        gaps.append(
            EvidenceGap(
                rule_id=rule.id,
                title=rule.title,
                necessity=rule.necessity,
                missing_fields=missing,
                citation=rule.citation,
                excused_by=excuse if excuse and rule.necessity is Necessity.REQUIRED else None,
            )
        )
    return gaps


def blocking_gaps(gaps: list[EvidenceGap]) -> list[EvidenceGap]:
    """Gaps serious enough to refuse the case over: required evidence that is
    missing and not excused by a satisfied alternative."""
    return [g for g in gaps if g.necessity is Necessity.REQUIRED and g.excused_by is None]
