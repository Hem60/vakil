"""The provenance gate.

A drafter proposes sentences. This decides which of them may be filed.

Every factual sentence must name the facts it rests on and state the values it
asserts. The gate resolves each citation against the fact index and checks the
asserted value matches. A claim whose citation does not resolve, or whose value
the record does not support, is **removed from the letter** - not flagged, not
softened, not left in with a footnote. The letter that gets filed contains only
sentences that survived.

Two properties follow, and both are the point:

1. **Deleting a document deletes the sentences that depended on it.** Remove a
   courier record and its facts leave the index; every claim citing them stops
   resolving and disappears from the letter. Nothing is invented to fill the
   gap, because the drafter has no mechanism for inventing - it can only cite.

2. **The generator is a model and the gate is not.** Asking a second model
   whether the first model's prose "seems supported" is marking your own
   homework. Resolving a citation against a closed set of facts is a lookup: it
   either resolves or it does not, the same way every time, and a reviewer can
   read the forty lines that decide.

Non-factual sentences - framing, requests, courtesies - carry no citation
because they assert nothing. The gate holds them to a different rule: they must
contain no dates, amounts or identifiers. A "courtesy" sentence carrying a date
is a factual claim wearing a disguise, and it is stripped too.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from vakil.draft.facts import FactIndex
from vakil.models import Claim, ClaimStatus

#: Patterns that look like asserted fact regardless of the sentence around them.
#: Used only on claims that declared themselves non-factual - if one of these
#: appears there, the declaration was wrong.
FACT_SHAPED = re.compile(
    r"""
    \d{4}-\d{2}-\d{2}          # an ISO date
  | \d{1,2}\s+\w+\s+\d{4}      # 19 August 2026
  | (?:rs\.?|inr|₹)\s*[\d,]+   # an amount
  | \b[A-Z]{2,4}\d{6,}\b       # a tracking id
  | \b(?:disp|pay|ord)_\w+     # a Razorpay or order identifier
    """,
    re.IGNORECASE | re.VERBOSE,
)


class ClaimKind(StrEnum):
    #: Asserts something about the world. Must cite, must match.
    FACTUAL = "factual"
    #: Framing, request or courtesy. Must assert nothing checkable.
    ARGUMENT = "argument"


class DraftedClaim(BaseModel):
    """One sentence, as proposed by a drafter and before verification."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(description="The sentence exactly as it should read in the letter.")
    kind: ClaimKind = Field(
        description="factual if it asserts something about this case; argument otherwise."
    )
    cites: tuple[str, ...] = Field(
        default=(),
        description="Fact ids this sentence rests on. Required for factual claims.",
    )
    asserts: tuple[str, ...] = Field(
        default=(),
        description=(
            "The values this sentence states, one per cited fact and in the same "
            "order - the date, the address, the amount as written in the sentence."
        ),
    )


class GateResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    claims: tuple[Claim, ...]

    @property
    def verified(self) -> tuple[Claim, ...]:
        return tuple(c for c in self.claims if c.status is ClaimStatus.VERIFIED)

    @property
    def stripped(self) -> tuple[Claim, ...]:
        return tuple(c for c in self.claims if c.status is ClaimStatus.STRIPPED)

    @property
    def strip_rate(self) -> float:
        return len(self.stripped) / len(self.claims) if self.claims else 0.0

    def body(self) -> str:
        """The filed letter: surviving sentences, in the order proposed."""
        return " ".join(c.text for c in self.verified)


def _verify_factual(claim: DraftedClaim, index: FactIndex) -> Claim:
    if not claim.cites:
        return Claim(
            text=claim.text,
            cited_source=None,
            cited_span=None,
            status=ClaimStatus.STRIPPED,
            note="factual claim with no citation",
        )

    if claim.asserts and len(claim.asserts) != len(claim.cites):
        return Claim(
            text=claim.text,
            cited_source=None,
            cited_span=None,
            status=ClaimStatus.STRIPPED,
            note=(
                f"{len(claim.cites)} citations but {len(claim.asserts)} asserted "
                "values - cannot tell which value belongs to which fact"
            ),
        )

    sources: list[str] = []
    spans: list[str] = []
    for position, fact_id in enumerate(claim.cites):
        fact = index.get(fact_id)
        if fact is None:
            return Claim(
                text=claim.text,
                cited_source=None,
                cited_span=None,
                status=ClaimStatus.STRIPPED,
                note=f"cites {fact_id}, which this case has no evidence for",
            )
        if claim.asserts:
            asserted = claim.asserts[position]
            if not index.supports(fact_id, asserted):
                return Claim(
                    text=claim.text,
                    cited_source=fact.source,
                    cited_span=fact.quote,
                    status=ClaimStatus.STRIPPED,
                    note=(
                        f"asserts {asserted!r} for {fact_id}, "
                        f"but the record says {fact.value!r}"
                    ),
                )
        sources.append(fact.source)
        if fact.quote:
            spans.append(fact.quote)

    return Claim(
        text=claim.text,
        cited_source="; ".join(dict.fromkeys(sources)),
        cited_span="; ".join(spans) or None,
        status=ClaimStatus.VERIFIED,
    )


def _verify_argument(claim: DraftedClaim) -> Claim:
    match = FACT_SHAPED.search(claim.text)
    if match:
        return Claim(
            text=claim.text,
            cited_source=None,
            cited_span=None,
            status=ClaimStatus.STRIPPED,
            note=(
                f"declared non-factual but states {match.group(0)!r} - "
                "a factual claim wearing a disguise"
            ),
        )
    return Claim(
        text=claim.text, cited_source=None, cited_span=None, status=ClaimStatus.VERIFIED
    )


def apply_gate(claims: tuple[DraftedClaim, ...], index: FactIndex) -> GateResult:
    """Verify every proposed sentence. Survivors form the filed letter."""
    checked = tuple(
        _verify_factual(c, index) if c.kind is ClaimKind.FACTUAL else _verify_argument(c)
        for c in claims
    )
    return GateResult(claims=checked)
