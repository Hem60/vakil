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
    vakil_extract_model: str = "claude-sonnet-5"

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
