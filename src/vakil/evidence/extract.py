"""Read a proof-of-delivery document.

This is one of exactly two places in Vakil where a model touches a case, and
its output is deliberately narrow: **the model transcribes, it does not
decide.** It returns what it can read, a verbatim quote of where it read it,
and an explicit admission when a field is illegible. Every downstream judgement
- is this the order address, was it delivered before the dispute, does the date
parse - is made by `to_delivery_proof()` below, in ordinary Python.

That split is why the provenance gate on day 7 can work at all. A claim in the
rebuttal letter can only survive if it traces to a `source_quote` that a model
said it actually saw on the page.

The other deliberate choice is abstention. The prompt tells the model to return
null rather than guess, and the extraction eval scores *wrong* separately from
*abstained*. On a photograph of a crumpled courier slip, a system that says "I
cannot read the signature" is worth more than one that invents a plausible
name, because the invented name would end up in a document filed with a bank.
"""

from __future__ import annotations

import base64
from datetime import datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

from vakil.config import Settings
from vakil.models import DeliveryProof

#: Kept small on purpose. Every field here is something a courier prints on a
#: POD; nothing here asks the model for an opinion.
EXTRACTION_PROMPT = """\
You are reading a courier proof-of-delivery document for a payment dispute.

Transcribe these fields exactly as they appear on the page:
  - tracking_id
  - carrier
  - delivered_at (the delivery date)
  - signed_by (the name of the person who received it)
  - delivered_to_address

Rules, in order of importance:

1. If a field is absent, illegible, or you are not confident you have read it
   correctly, set its value to null and set legible to false. Do not guess. Do
   not infer a value from context or from what is typical. A null is a correct
   answer; an invented value is a serious error, because this document will be
   used as evidence in a filing to a bank.

2. For every field you do read, put the literal characters as printed on the
   page into source_quote - not a cleaned-up or reformatted version. This is
   what makes the extraction auditable.

3. Some documents state explicitly that no signature was obtained (for example
   "LEFT AT DOOR - NO SIGNATURE"). That is a legible statement that there is no
   signature: set signed_by value to null, legible to true, and quote the text.
   That is different from a signature you simply cannot make out.

4. Ignore the barcode and any DELIVERED stamp overlay. They carry no field data.
"""


class ExtractedField(BaseModel):
    """One field, plus where it came from and whether it could be read."""

    value: str | None = Field(
        description="The transcribed value, or null if absent or illegible."
    )
    source_quote: str | None = Field(
        description="Literal text as printed on the page, or null if nothing was read."
    )
    legible: bool = Field(
        description="True if the page states this clearly, including an explicit "
        "statement that no signature was taken. False if illegible or absent."
    )


class ExtractedPOD(BaseModel):
    tracking_id: ExtractedField
    carrier: ExtractedField
    delivered_at: ExtractedField
    signed_by: ExtractedField
    delivered_to_address: ExtractedField
    notes: str = Field(
        default="",
        description="Anything about document quality worth recording. Keep it short.",
    )

    def source_spans(self) -> dict[str, str]:
        spans = {}
        for name in ("tracking_id", "carrier", "delivered_at", "signed_by",
                     "delivered_to_address"):
            field: ExtractedField = getattr(self, name)
            if field.source_quote:
                spans[name] = field.source_quote
        return spans


class ExtractionResult(BaseModel):
    document_uri: str
    extracted: ExtractedPOD
    model: str
    input_tokens: int = 0
    output_tokens: int = 0

    def cost_paise(self, input_per_mtok: float, output_per_mtok: float) -> int:
        """Rupees per case is a number the eval report quotes, so it is computed
        from real usage rather than estimated."""
        usd = (
            self.input_tokens * input_per_mtok + self.output_tokens * output_per_mtok
        ) / 1_000_000
        return round(usd * 88 * 100)  # USD -> INR -> paise


class Extractor(Protocol):
    def extract(self, document_path: Path) -> ExtractionResult: ...


# ---------------------------------------------------------------------------
# Deterministic post-processing. No model involved past this line.
# ---------------------------------------------------------------------------

#: Date formats a courier might print. Tried in order; anything else is treated
#: as unparseable rather than coerced, because a wrong delivery date is worse
#: than a missing one.
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y")


def parse_delivery_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text = raw.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text[:len(datetime.now().strftime(fmt)) + 4], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def to_delivery_proof(result: ExtractionResult) -> DeliveryProof | None:
    """Turn a transcription into the domain object the decision path consumes.

    Returns None when the document yielded nothing usable. A POD with no
    tracking id and no delivery date is not weak evidence, it is no evidence,
    and passing it downstream would let the win model score a blank page.
    """
    e = result.extracted
    delivered_at = parse_delivery_date(e.delivered_at.value)
    if not e.tracking_id.value and delivered_at is None:
        return None

    return DeliveryProof(
        tracking_id=e.tracking_id.value or "",
        delivered_at=delivered_at,
        # An illegible signature must not become a signature. Only a value the
        # model marked legible counts.
        signed_by=e.signed_by.value if e.signed_by.legible else None,
        delivered_to_address=(
            e.delivered_to_address.value if e.delivered_to_address.legible else None
        ),
        carrier=e.carrier.value or "unknown",
        document_uri=result.document_uri,
        source_spans=e.source_spans(),
    )


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


class ClaudeExtractor:
    """Reads the PDF directly - no OCR step, no image conversion.

    The Messages API accepts a PDF as a base64 `document` block, and the pages
    here are single-image scans, so handing over the whole file lets the model
    see the layout, the stamp overlay and the handwriting together. An OCR
    pre-pass would flatten exactly the visual context that makes a degraded
    photo readable at all.
    """

    def __init__(self, settings: Settings, client: object | None = None) -> None:
        self._settings = settings
        self._client = client

    def _get_client(self) -> object:
        if self._client is None:
            import anthropic

            if not self._settings.anthropic_api_key:
                raise RuntimeError(
                    "ANTHROPIC_API_KEY is not set - put it in .env line 8. "
                    "Everything except extraction and drafting runs without it."
                )
            self._client = anthropic.Anthropic(api_key=self._settings.anthropic_api_key)
        return self._client

    def extract(self, document_path: Path) -> ExtractionResult:
        # Client first, document second. A missing key is a configuration
        # error and should say so; reading the file first makes it surface as
        # a confusing FileNotFoundError from whatever path happened to be tried.
        client = self._get_client()
        data = base64.standard_b64encode(document_path.read_bytes()).decode()

        response = client.messages.parse(  # type: ignore[attr-defined]
            model=self._settings.vakil_extract_model,
            max_tokens=4000,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": data,
                            },
                        },
                        {"type": "text", "text": EXTRACTION_PROMPT},
                    ],
                }
            ],
            output_format=ExtractedPOD,
        )

        return ExtractionResult(
            document_uri=str(document_path),
            extracted=response.parsed_output,
            model=self._settings.vakil_extract_model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )


class StubExtractor:
    """Returns a fixed transcription without calling anything.

    Exists so the pipeline, the tests and CI exercise the extraction path with
    no API key and no spend. It is not a fallback - nothing selects it
    automatically, because a silent stub would make a broken key look like a
    working system that simply found nothing.
    """

    def __init__(self, canned: ExtractedPOD | None = None) -> None:
        def read(value: str) -> ExtractedField:
            return ExtractedField(value=value, source_quote=value, legible=True)

        unread = ExtractedField(value=None, source_quote=None, legible=False)
        self._canned = canned or ExtractedPOD(
            tracking_id=read("STUB000000"),
            carrier=read("Stub Logistics"),
            delivered_at=read("2026-08-19"),
            signed_by=unread,
            delivered_to_address=unread,
            notes="stub extractor - no document was read",
        )

    def extract(self, document_path: Path) -> ExtractionResult:
        return ExtractionResult(
            document_uri=str(document_path), extracted=self._canned, model="stub"
        )
