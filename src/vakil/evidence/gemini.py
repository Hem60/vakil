"""Gemini extraction backend.

Why this exists, stated plainly because a mixed stack needs a reason: the
Anthropic account funding this project has no credit, and Track 2 asks for
*measured* results. An unmeasured extraction stage is worse than one measured
on a different vendor's model. Gemini's free tier reads PDFs and returns
structured JSON, so the stage can be scored today rather than described.

It implements the same `Extractor` protocol as `ClaudeExtractor` and returns
the same `ExtractionResult`, so nothing downstream knows or cares which one
ran. The extraction eval reports whichever backend produced the numbers, and
`docs/DECISIONS.md` D10 records the trade.

Raw HTTP via httpx rather than the `google-genai` SDK: this is a secondary
backend behind a two-method protocol, and one REST call is a smaller thing to
own than another dependency.
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any

import httpx

from vakil.config import Settings
from vakil.evidence.extract import (
    EXTRACTION_PROMPT,
    ExtractedPOD,
    ExtractionResult,
)

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

#: Gemini accepts an OpenAPI subset, not full JSON Schema - no $defs, no $ref,
#: and nullability is a flag rather than a union. Written out by hand for that
#: reason; deriving it from the Pydantic model would emit constructs the API
#: rejects.
_FIELD_SCHEMA = {
    "type": "object",
    "properties": {
        "value": {"type": "string", "nullable": True},
        "source_quote": {"type": "string", "nullable": True},
        "legible": {"type": "boolean"},
    },
    "required": ["value", "source_quote", "legible"],
}

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tracking_id": _FIELD_SCHEMA,
        "carrier": _FIELD_SCHEMA,
        "delivered_at": _FIELD_SCHEMA,
        "signed_by": _FIELD_SCHEMA,
        "delivered_to_address": _FIELD_SCHEMA,
        "notes": {"type": "string"},
    },
    "required": [
        "tracking_id",
        "carrier",
        "delivered_at",
        "signed_by",
        "delivered_to_address",
        "notes",
    ],
}

#: Free-tier keys return 429 rather than queueing. Retries are spaced widely
#: because the limit is per minute, so a fast retry just burns another slot.
RETRY_DELAYS = (20.0, 45.0, 90.0)

#: Granular timeouts rather than one number. A bare `timeout=120` did not stop
#: a run that hung for four hours: the connect phase is where a blackholed
#: route stalls, and it needs its own short ceiling. Read is generous because a
#: large PDF genuinely takes time to process; connect is not, because a
#: connection that has not opened in ten seconds is not going to.
TIMEOUT = httpx.Timeout(connect=10.0, read=90.0, write=30.0, pool=10.0)


class GeminiError(RuntimeError):
    pass


class GeminiUnavailable(GeminiError):
    """The API could not be reached at all - timeout, DNS, refused connection.

    Distinct from a rejected request because the caller should treat it
    differently: one malformed document is worth skipping, but a network that
    is not there will not fix itself over the next 174 documents.
    """


#: Markers for an allowance that will not come back today, as opposed to a
#: per-minute limit that clears on its own.
#:
#: The first version matched "exceeded your current quota", which was wrong in
#: the expensive direction. Google uses that same wording for **both** kinds of
#: 429, so a per-minute rate limit was being classified as terminal and
#: aborting runs that would have finished. The tell is the metric name: a daily
#: allowance names a per-day metric, a rate limit does not.
DAILY_QUOTA_MARKERS = (
    "per_day",
    "perday",
    "per day",
    "daily limit",
    "requests per day",
)


def _is_quota_exhausted(body: str) -> bool:
    """True only when the allowance is gone until tomorrow.

    Deliberately conservative: a false positive here stops a run that would
    have succeeded, and a false negative only costs one retry ladder. Anything
    that is merely a 429 is treated as a rate limit and retried.
    """
    lowered = body.lower()
    return any(marker in lowered for marker in DAILY_QUOTA_MARKERS)


class GeminiExtractor:
    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self._settings = settings
        self._client = client
        self._min_interval = 60.0 / max(settings.vakil_gemini_rpm, 1)
        self._last_call = 0.0

    # ------------------------------------------------------------- throttle

    def _wait_for_slot(self) -> None:
        """Space calls to the configured rate rather than discovering the limit
        through a wall of 429s. On a 175-document run this is the difference
        between finishing and being throttled into failure."""
        elapsed = time.monotonic() - self._last_call
        if self._last_call and elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_call = time.monotonic()

    # -------------------------------------------------------------- request

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._settings.gemini_api_key:
            raise GeminiError(
                "GEMINI_API_KEY is not set - put it in .env. "
                "Get one at aistudio.google.com/apikey"
            )
        if self._client is None:
            # One client, one connection pool. The previous version built a
            # fresh client per call, which threw away every kept-alive
            # connection and its TLS handshake.
            self._client = httpx.Client(timeout=TIMEOUT)
        client = self._client
        url = ENDPOINT.format(model=self._settings.vakil_gemini_model)

        last_error = ""
        for attempt, delay in enumerate((0.0, *RETRY_DELAYS)):
            if delay:
                time.sleep(delay)
            self._wait_for_slot()
            try:
                response = client.post(
                    url,
                    params={"key": self._settings.gemini_api_key},
                    json=payload,
                    headers={"content-type": "application/json"},
                )
            except httpx.TimeoutException as exc:
                raise GeminiUnavailable(f"request timed out: {exc}") from exc
            except httpx.TransportError as exc:
                raise GeminiUnavailable(f"could not reach the API: {exc}") from exc

            if response.status_code == 200:
                return dict(response.json())
            last_error = f"HTTP {response.status_code}: {response.text[:400]}"

            # A 429 comes in two flavours and they need opposite handling. A
            # per-minute rate limit clears on its own, so waiting is correct.
            # An exhausted daily quota does not clear for hours, and retrying
            # it cost this project a four-hour run that produced nothing -
            # every request was held ~170s before being refused, three times
            # per document, 175 documents deep.
            if response.status_code == 429 and _is_quota_exhausted(response.text):
                raise GeminiUnavailable(f"free-tier quota exhausted: {last_error}")

            # Rate limits and transient server errors are worth waiting out.
            # A 400 or 403 is a request or key problem and will not improve.
            if response.status_code not in (429, 500, 503):
                break
            if attempt == len(RETRY_DELAYS):
                break

        raise GeminiError(last_error)

    # -------------------------------------------------------------- extract

    def extract(self, document_path: Path) -> ExtractionResult:
        data = base64.standard_b64encode(document_path.read_bytes()).decode()
        payload = {
            "contents": [
                {
                    "parts": [
                        {"inline_data": {"mime_type": "application/pdf", "data": data}},
                        {"text": EXTRACTION_PROMPT},
                    ]
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": RESPONSE_SCHEMA,
                "temperature": 0.0,
            },
        }

        body = self._post(payload)
        try:
            candidate = body["candidates"][0]
            text = candidate["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            # A blocked or empty candidate has no parts. Surface it rather than
            # letting it read as a document that contained nothing.
            raise GeminiError(f"no candidate content: {json.dumps(body)[:400]}") from exc

        extracted = ExtractedPOD.model_validate_json(text)
        usage = body.get("usageMetadata", {})

        return ExtractionResult(
            document_uri=str(document_path),
            extracted=extracted,
            model=self._settings.vakil_gemini_model,
            input_tokens=int(usage.get("promptTokenCount", 0)),
            output_tokens=int(usage.get("candidatesTokenCount", 0)),
        )
