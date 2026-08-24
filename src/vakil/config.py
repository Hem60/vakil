"""Runtime configuration. Economics live here so the pitch can move one slider."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    razorpay_key_id: str = "rzp_test_placeholder"
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""
    razorpay_base_url: str = "http://localhost:8080"

    anthropic_api_key: str = ""
    vakil_draft_model: str = "claude-opus-5"
    #: Extraction reads degraded scans and phone photographs, so it defaults
    #: to the strongest model. Downgrading to claude-sonnet-5 costs about a
    #: third as much; the extraction eval reports accuracy per quality tier
    #: so that trade can be made on measurements rather than on vibes.
    vakil_extract_model: str = "claude-opus-5"

    # --- Gemini, second extraction backend ---
    #: Present because the Anthropic account has no credit, not because a
    #: mixed stack is desirable. The extraction eval reports both backends
    #: side by side so the choice rests on measurements. See D10.
    gemini_api_key: str = ""
    vakil_gemini_model: str = "gemini-2.5-flash"
    #: Free-tier requests per minute. The extractor throttles to this rather
    #: than discovering the limit through a wall of 429s.
    vakil_gemini_rpm: int = 10

    database_url: str = "postgresql://vakil:vakil@localhost:5432/vakil"

    # --- Fight-or-Fold economics, all paise ---
    vakil_representment_cost: int = 25_000
    vakil_arbitration_exposure: int = 80_000

    # Merchant's current dispute ratio, and the VAMP threshold it is judged against.
    # As the ratio approaches the threshold, each additional dispute filed carries a
    # rising penalty, because crossing it triggers network fines and T&C review.
    vakil_dispute_ratio: float = 0.0040
    vakil_vamp_threshold: float = 0.0090
    vakil_vamp_max_penalty: int = 150_000

    # Autonomy gate
    vakil_autofile_max_amount: int = 1_000_000
    vakil_autofile_min_confidence: float = 0.75


@lru_cache
def settings() -> Settings:
    return Settings()
