"""Drafting the rebuttal.

Three drafters, one interface. All of them produce `DraftedClaim`s - sentences
that name the facts they rest on - and all of them are then checked by the same
gate. A drafter cannot file anything; it can only propose.

`TemplateDrafter` builds the letter from the fact index directly, in fixed
order, with no model involved. It exists for three reasons and only the first
is convenience:

  1. It runs with no API key, so the whole path is exercised in CI.
  2. It is the **baseline**. If a generated letter does not beat a mechanical
     one on the metrics that matter - claims verified, facts used, strip rate -
     then the model is decoration and the honest thing is to say so.
  3. It is the **deadline fallback**. Four hours before a response window
     closes, a plain letter that files is worth more than an elegant one that
     is still waiting on a rate limit.

By construction it cannot hallucinate: every sentence is generated *from* a
fact, so a fact that is absent produces no sentence. The model drafters have no
such guarantee, which is what the gate is for.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from vakil.config import Settings
from vakil.draft.facts import FactIndex
from vakil.draft.gate import ClaimKind, DraftedClaim, GateResult, apply_gate
from vakil.models import Dispute
from vakil.rulebook.store import Rule


class Drafter(Protocol):
    def draft(
        self, dispute: Dispute, index: FactIndex, requirements: list[Rule]
    ) -> tuple[DraftedClaim, ...]: ...


def compose(
    drafter: Drafter, dispute: Dispute, index: FactIndex, requirements: list[Rule]
) -> GateResult:
    """Propose, then verify. The only entry point anything else should use."""
    return apply_gate(drafter.draft(dispute, index, requirements), index)


# ---------------------------------------------------------------------------
# Deterministic
# ---------------------------------------------------------------------------

#: (fact ids, sentence template). Rendered only when every id is present, so a
#: missing document silently removes its sentence rather than leaving a hole.
SENTENCE_PLAN: tuple[tuple[tuple[str, ...], str], ...] = (
    (("order.id", "order.placed_at"), "Order {0} was placed on {1}."),
    (("order.items",), "The order was for {0}."),
    (("order.amount",), "The order value was Rs {0}."),
    (
        ("delivery.delivered_at", "delivery.delivered_to_address"),
        "The merchandise was delivered on {0} to {1}, the address on the order.",
    ),
    (("delivery.signed_by",), "Delivery was signed for by {0}."),
    (("delivery.tracking_id", "delivery.carrier"), "Carrier {1} recorded this under tracking {0}."),
    (
        ("ce3.matched_identifiers",),
        "Prior undisputed transactions by this cardholder share {0} with the disputed one.",
    ),
    (("history.prior_undisputed",), "This cardholder has {0} earlier undisputed transactions."),
    (("support.first_contact_at",), "The cardholder first contacted support on {0}."),
    (("refund.confirmation",), "A refund was {0} for this order."),
)

CLOSING = (
    "On this evidence the merchant respectfully submits that the disputed "
    "transaction was authorised and fulfilled as agreed, and asks that the "
    "dispute be resolved in its favour."
)


class TemplateDrafter:
    """Assembles sentences from facts. Cannot assert what it does not hold."""

    def draft(
        self, dispute: Dispute, index: FactIndex, requirements: list[Rule]
    ) -> tuple[DraftedClaim, ...]:
        claims: list[DraftedClaim] = []

        for fact_ids, template in SENTENCE_PLAN:
            facts = [index.get(fact_id) for fact_id in fact_ids]
            if any(f is None for f in facts):
                continue  # the evidence for this sentence is not held
            values = [f.value for f in facts if f is not None]
            claims.append(
                DraftedClaim(
                    text=template.format(*values),
                    kind=ClaimKind.FACTUAL,
                    cites=fact_ids,
                    asserts=tuple(values),
                )
            )

        if claims:
            claims.append(DraftedClaim(text=CLOSING, kind=ClaimKind.ARGUMENT))
        return tuple(claims)


# ---------------------------------------------------------------------------
# Model-backed
# ---------------------------------------------------------------------------

DRAFT_PROMPT = """\
You are drafting a merchant's rebuttal letter contesting a payment dispute.

Dispute condition {reason_code}: {reason_description}

The network requires the following for this condition:
{requirements}

These are the ONLY facts established for this case. Each line is
`id = value  [source]`:

{facts}

Write the letter as a list of claims, one sentence each.

Rules, and the first is absolute:

1. **You may not state anything that is not in the fact list above.** Not an
   inference, not a detail that is probably true, not a rounding. If the list
   has no delivery date, the letter says nothing about when anything arrived.
   A missing fact means a missing sentence, never a guessed one.

2. Every sentence that asserts something about this case has kind "factual",
   lists the fact ids it rests on in `cites`, and puts the values exactly as
   your sentence states them in `asserts`, one per cited id, same order.

3. Sentences that argue, request or frame have kind "argument" and no
   citations - and must contain no dates, amounts, tracking numbers or
   identifiers. Put those in factual sentences where they can be checked.

4. Write plainly, for a bank reviewer with sixty seconds. Lead with the
   strongest evidence. No filler, no adjectives doing work that facts should do.

A verification step will remove any sentence whose citation does not resolve or
whose asserted value does not match the record. A stripped sentence is a
sentence that never reaches the issuer, so citing carefully is not bureaucracy -
it is how your argument survives.
"""


def build_prompt(dispute: Dispute, index: FactIndex, requirements: list[Rule]) -> str:
    filable = [r for r in requirements if r.is_filable]
    return DRAFT_PROMPT.format(
        reason_code=dispute.reason_code,
        reason_description=dispute.reason_description or "",
        requirements="\n".join(f"  - {r.title}: {r.requirement}" for r in filable) or "  (none)",
        facts=index.render() or "  (no facts established)",
    )


#: Gemini takes an OpenAPI subset, so the schema is written out rather than
#: derived from the Pydantic model - same constraint as the extraction backend.
CLAIMS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "kind": {"type": "string", "enum": ["factual", "argument"]},
                    "cites": {"type": "array", "items": {"type": "string"}},
                    "asserts": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "kind", "cites", "asserts"],
            },
        }
    },
    "required": ["claims"],
}


def _parse(payload: dict[str, Any]) -> tuple[DraftedClaim, ...]:
    return tuple(DraftedClaim.model_validate(c) for c in payload.get("claims", []))


class GeminiDrafter:
    """Reuses the extraction backend's transport, throttle and circuit breaker
    rather than opening a second, differently-behaved path to the same API."""

    def __init__(self, settings: Settings, client: object | None = None) -> None:
        from vakil.evidence.gemini import GeminiExtractor

        self._settings = settings
        self._transport = GeminiExtractor(settings, client)  # type: ignore[arg-type]

    def draft(
        self, dispute: Dispute, index: FactIndex, requirements: list[Rule]
    ) -> tuple[DraftedClaim, ...]:
        body = self._transport._post(  # noqa: SLF001 - deliberate reuse, see class docstring
            {
                "contents": [
                    {"parts": [{"text": build_prompt(dispute, index, requirements)}]}
                ],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseSchema": CLAIMS_SCHEMA,
                    "temperature": 0.0,
                },
            }
        )
        text = body["candidates"][0]["content"]["parts"][0]["text"]
        return _parse(json.loads(text))


class ClaudeDrafter:
    def __init__(self, settings: Settings, client: object | None = None) -> None:
        self._settings = settings
        self._client = client

    def draft(
        self, dispute: Dispute, index: FactIndex, requirements: list[Rule]
    ) -> tuple[DraftedClaim, ...]:
        if self._client is None:
            import anthropic

            if not self._settings.anthropic_api_key:
                raise RuntimeError("ANTHROPIC_API_KEY is not set - put it in .env")
            self._client = anthropic.Anthropic(api_key=self._settings.anthropic_api_key)

        response = self._client.messages.create(  # type: ignore[attr-defined]
            model=self._settings.vakil_draft_model,
            max_tokens=4000,
            thinking={"type": "adaptive"},
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": CLAIMS_SCHEMA},
            },
            messages=[
                {"role": "user", "content": build_prompt(dispute, index, requirements)}
            ],
        )
        text = next(b.text for b in response.content if b.type == "text")
        return _parse(json.loads(text))
