"""Gemini backend tests.

Driven through a mocked httpx transport rather than the network, so the request
shape, the retry policy and the failure modes are all pinned without a key and
without burning free-tier quota.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from vakil.config import Settings
from vakil.evidence.extract import to_delivery_proof
from vakil.evidence.gemini import RESPONSE_SCHEMA, GeminiError, GeminiExtractor

FIXTURE = Path("data/fixtures/pod")


def settings(**overrides: object) -> Settings:
    base = Settings(
        gemini_api_key="test-key",
        vakil_gemini_model="gemini-3.6-flash",
        vakil_gemini_rpm=6000,  # effectively no throttle inside tests
    )
    return base.model_copy(update=overrides)


def good_payload() -> dict:
    def field(value: str | None, legible: bool = True) -> dict:
        return {"value": value, "source_quote": value, "legible": legible}

    return {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": json.dumps(
                                {
                                    "tracking_id": field("EKA335441403"),
                                    "carrier": field("Ekart"),
                                    "delivered_at": field("2026-08-19"),
                                    "signed_by": field(None, legible=False),
                                    "delivered_to_address": field("62 Residency Lane"),
                                    "notes": "photo, low contrast",
                                }
                            )
                        }
                    ]
                }
            }
        ],
        "usageMetadata": {"promptTokenCount": 1800, "candidatesTokenCount": 220},
    }


def transport(handler) -> httpx.Client:  # type: ignore[no-untyped-def]
    return httpx.Client(transport=httpx.MockTransport(handler))


def any_document() -> Path:
    candidates = sorted(FIXTURE.glob("*.pdf"))
    if not candidates:
        pytest.skip("no fixtures rendered - run make fixtures")
    return candidates[0]


# ------------------------------------------------------------- request shape


def test_sends_the_pdf_inline_with_a_response_schema():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=good_payload())

    GeminiExtractor(settings(), transport(handler)).extract(any_document())

    assert "gemini-3.6-flash:generateContent" in seen["url"]
    assert "key=test-key" in seen["url"]
    parts = seen["body"]["contents"][0]["parts"]
    assert parts[0]["inline_data"]["mime_type"] == "application/pdf"
    assert parts[0]["inline_data"]["data"]
    config = seen["body"]["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseSchema"] == RESPONSE_SCHEMA
    assert config["temperature"] == 0.0


def test_schema_marks_values_nullable():
    """The model must be able to say "I could not read this". A schema that
    requires a string would force it to invent one."""
    field = RESPONSE_SCHEMA["properties"]["signed_by"]
    assert field["properties"]["value"]["nullable"] is True
    assert "legible" in field["required"]


# ------------------------------------------------------------------ parsing


def test_parses_a_well_formed_response():
    result = GeminiExtractor(
        settings(), transport(lambda r: httpx.Response(200, json=good_payload()))
    ).extract(any_document())

    assert result.extracted.tracking_id.value == "EKA335441403"
    assert result.model == "gemini-3.6-flash"
    assert result.input_tokens == 1800
    assert result.output_tokens == 220


def test_free_tier_reports_zero_cost_rather_than_an_imputed_price():
    result = GeminiExtractor(
        settings(), transport(lambda r: httpx.Response(200, json=good_payload()))
    ).extract(any_document())
    assert result.cost_paise(0.0, 0.0) == 0


def test_illegible_field_survives_into_the_domain_object():
    result = GeminiExtractor(
        settings(), transport(lambda r: httpx.Response(200, json=good_payload()))
    ).extract(any_document())
    proof = to_delivery_proof(result)
    assert proof is not None
    assert proof.signed_by is None


# ----------------------------------------------------------------- failures


def test_missing_key_says_so_before_any_request():
    with pytest.raises(GeminiError, match="GEMINI_API_KEY"):
        GeminiExtractor(settings(gemini_api_key="")).extract(any_document())


def test_blocked_response_is_an_error_not_an_empty_document():
    """A safety-blocked candidate has no parts. Treating that as "the page was
    blank" would quietly downgrade real evidence to none."""
    blocked = {"candidates": [{"finishReason": "SAFETY"}]}
    with pytest.raises(GeminiError, match="no candidate content"):
        GeminiExtractor(
            settings(), transport(lambda r: httpx.Response(200, json=blocked))
        ).extract(any_document())


def test_rate_limit_is_retried():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, text="rate limited")
        return httpx.Response(200, json=good_payload())

    extractor = GeminiExtractor(settings(), transport(handler))
    extractor._min_interval = 0.0  # noqa: SLF001 - skip the sleep in tests
    import vakil.evidence.gemini as mod

    original, mod.RETRY_DELAYS = mod.RETRY_DELAYS, (0.0,)
    try:
        result = extractor.extract(any_document())
    finally:
        mod.RETRY_DELAYS = original

    assert calls["n"] == 2
    assert result.extracted.carrier.value == "Ekart"


def test_bad_key_is_not_retried():
    """A 403 will not improve by waiting, and retrying it just burns quota."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(403, text="API key not valid")

    with pytest.raises(GeminiError, match="403"):
        GeminiExtractor(settings(), transport(handler)).extract(any_document())
    assert calls["n"] == 1
