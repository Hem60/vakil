"""The fact index: everything this case is entitled to assert.

Built deterministically from the harvested evidence, before any letter exists.
Nothing enters it that was not read off a document or a record, and every entry
carries where it came from.

This is what makes the provenance gate exact rather than approximate. A gate
that reads free prose and judges whether it "seems supported" is a second model
marking the first model's homework. A gate that checks a claim's citation
against a closed set of facts is a lookup, and it either resolves or it does
not.

The consequence worth stating: **if a fact is not in this index, no sentence in
the filed letter may assert it.** Delete a courier document from a case and its
facts disappear from the index; any claim citing them then fails to verify and
is removed. That is the whole mechanism.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from vakil.models import CE3Result, Dispute, EvidenceBundle


class Fact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Dotted path a drafted claim cites, e.g. "delivery.delivered_at".
    id: str
    #: The value as it may be asserted, normalised for comparison.
    value: str
    #: Human-readable provenance, shown in the audit trail.
    source: str
    #: Verbatim text from the source document, when the value came from one.
    #: Populated by extraction; None for values read from structured records.
    quote: str | None = None

    def render(self) -> str:
        where = f"{self.source}" + (f' - "{self.quote}"' if self.quote else "")
        return f"{self.id} = {self.value}  [{where}]"


def normalise(value: str) -> str:
    """Comparison form. Case, commas and runs of whitespace carry no meaning
    when checking whether a letter asserts what a document says."""
    return " ".join(str(value).lower().replace(",", " ").split())


class FactIndex(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    facts: tuple[Fact, ...] = ()

    def get(self, fact_id: str) -> Fact | None:
        for fact in self.facts:
            if fact.id == fact_id:
                return fact
        return None

    def ids(self) -> tuple[str, ...]:
        return tuple(f.id for f in self.facts)

    def supports(self, fact_id: str, asserted: str) -> bool:
        """Does the index contain this fact with this value?

        Substring rather than equality in one direction only: a letter may say
        "delivered to 62 Residency Lane, Jaipur 347264" when the record holds
        exactly that address, and may say less than the record, but may not say
        more. A claim asserting a value the record does not contain fails.
        """
        fact = self.get(fact_id)
        if fact is None:
            return False
        return normalise(asserted) in normalise(fact.value) or normalise(
            fact.value
        ) in normalise(asserted)

    def render(self) -> str:
        return "\n".join(f.render() for f in self.facts)


def _date(value: datetime | None) -> str | None:
    return value.date().isoformat() if value else None


def build_fact_index(
    dispute: Dispute, bundle: EvidenceBundle, ce3: CE3Result
) -> FactIndex:
    """Everything assertable about this case, and nothing else.

    Deliberately narrow. Fields the harvest could not establish are simply
    absent - there is no placeholder, no "unknown", nothing a drafter could
    mistake for permission to speculate.
    """
    facts: list[Fact] = []

    def add(fact_id: str, value: object | None, source: str, quote: str | None = None) -> None:
        if value is None or value == "":
            return
        facts.append(Fact(id=fact_id, value=str(value), source=source, quote=quote))

    # --- the dispute itself, from the acquirer ---------------------------
    add("dispute.id", dispute.id, "Razorpay dispute record")
    add("dispute.amount", f"{dispute.amount / 100:.2f}", "Razorpay dispute record")
    add("dispute.reason_code", dispute.reason_code, "Razorpay dispute record")
    add("dispute.raised_on", _date(dispute.created_at), "Razorpay dispute record")

    # --- order record ----------------------------------------------------
    if bundle.order:
        o = bundle.order
        add("order.id", o.id, "merchant order record")
        add("order.placed_at", _date(o.placed_at), "merchant order record")
        add("order.amount", f"{o.amount / 100:.2f}", "merchant order record")
        add("order.shipping_address", o.shipping_address, "merchant order record")
        add("order.billing_address", o.billing_address, "merchant order record")
        add("order.customer_email", o.customer_email, "merchant order record")
        if o.items:
            add(
                "order.items",
                "; ".join(f"{i.qty} x {i.title}" for i in o.items),
                "merchant order record",
            )

    # --- delivery, the part that comes from a scanned document -----------
    if bundle.delivery:
        d = bundle.delivery
        spans = d.source_spans
        src = f"courier proof of delivery ({d.carrier})"
        add("delivery.tracking_id", d.tracking_id, src, spans.get("tracking_id"))
        add("delivery.carrier", d.carrier, src, spans.get("carrier"))
        add("delivery.delivered_at", _date(d.delivered_at), src, spans.get("delivered_at"))
        add("delivery.signed_by", d.signed_by, src, spans.get("signed_by"))
        add(
            "delivery.delivered_to_address",
            d.delivered_to_address,
            src,
            spans.get("delivered_to_address"),
        )

    # --- support thread --------------------------------------------------
    if bundle.support:
        add("support.thread_id", bundle.support.id, "merchant support system")
        add("support.message_count", len(bundle.support.messages), "merchant support system")
        if bundle.support.messages:
            first = bundle.support.messages[0]
            add("support.first_contact_at", _date(first.at), "merchant support system")

    # --- policy ----------------------------------------------------------
    if bundle.policy:
        add("policy.refund_terms", bundle.policy.refund_policy_text, "merchant published policy")
        add(
            "policy.effective_from",
            _date(bundle.policy.effective_from),
            "merchant published policy",
        )

    # --- device and prior relationship -----------------------------------
    if bundle.device:
        add("device.id", bundle.device.device_id, "checkout device fingerprint")
        add("device.ip", bundle.device.ip_address, "checkout device fingerprint")

    if bundle.prior_transactions:
        undisputed = [t for t in bundle.prior_transactions if t.undisputed]
        add(
            "history.prior_transactions",
            len(bundle.prior_transactions),
            "merchant payment history",
        )
        add("history.prior_undisputed", len(undisputed), "merchant payment history")

    if ce3.qualifies:
        add("ce3.qualifies", "yes", ce3.citation or "Visa Compelling Evidence 3.0")
        add(
            "ce3.matched_identifiers",
            ", ".join(ce3.matched_identifiers),
            ce3.citation or "Visa Compelling Evidence 3.0",
        )

    if bundle.refund_confirmation_uri:
        add("refund.confirmation", "issued", "merchant refund record")

    return FactIndex(facts=tuple(facts))
