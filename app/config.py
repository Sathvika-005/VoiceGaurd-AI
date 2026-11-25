"""Application configuration loaded from environment variables."""

from __future__ import annotations

import secrets
from functools import lru_cache
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.services.aletheia import DEFAULT_ALETHEIA_API_URL, normalize_aletheia_api_url


class Settings(BaseSettings):
    """VoiceGuard AI runtime settings with safe local defaults."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Core AI credentials
    detectif_api_key: str = Field(default="")
    gemini_api_key: str = Field(default="")
    # Verified 2026-11-24 against Google AI Gemini models docs
    gemini_model: str = Field(default="gemini-3.8-flash")

    # Meta WhatsApp Cloud API credentials
    whatsapp_access_token: str = Field(default="")
    whatsapp_phone_number_id: str = Field(default="")
    whatsapp_verify_token: str = Field(default="")
    whatsapp_app_secret: str = Field(default="")
    # Verified 2026-11-24 against Meta Graph API changelog
    meta_graph_api_version: str = Field(default="v25.0")
    whatsapp_allowed_numbers: str = Field(default="")

    # Engine endpoints & audio processing limits
    aletheia_api_url: str = Field(default=DEFAULT_ALETHEIA_API_URL)

    @field_validator("aletheia_api_url", mode="before")
    @classmethod
    def _normalize_aletheia_url(cls, value: object) -> str:
        if value is None:
            return DEFAULT_ALETHEIA_API_URL
        return normalize_aletheia_api_url(str(value))
    max_upload_mb: int = Field(default=16, ge=1, le=100)
    min_audio_seconds: float = Field(default=4.0, ge=1.0)
    max_aletheia_seconds: float = Field(default=120.0, ge=10.0, le=120.0)
    max_total_audio_minutes: float = Field(default=10.0, ge=1.0, le=30.0)
    enable_segmentation: bool = Field(default=True)

    # Regional & localization
    region: str = Field(default="IN")
    default_report_language: str = Field(default="en")

    # Limits, caching & observability
    rate_limit_per_hour: int = Field(default=10, ge=1)
    max_concurrent_analyses: int = Field(default=3, ge=1, le=20)
    cache_ttl_seconds: int = Field(default=3600, ge=0)
    log_level: str = Field(default="INFO")
    debug_raw_responses: bool = Field(default=False)
    app_secret_key: str = Field(default_factory=lambda: secrets.token_hex(32))

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def max_total_audio_seconds(self) -> float:
        return self.max_total_audio_minutes * 60.0

    @property
    def aletheia_configured(self) -> bool:
        return bool(self.detectif_api_key.strip())

    @property
    def gemini_configured(self) -> bool:
        return bool(self.gemini_api_key.strip())

    @property
    def whatsapp_configured(self) -> bool:
        return bool(
            self.whatsapp_access_token.strip()
            and self.whatsapp_phone_number_id.strip()
        )

    @property
    def whatsapp_signature_enforced(self) -> bool:
        return bool(self.whatsapp_app_secret.strip())

    @property
    def allowed_numbers_set(self) -> set[str]:
        raw = self.whatsapp_allowed_numbers.strip()
        if not raw:
            return set()
        return {
            "".join(ch for ch in item.strip() if ch.isdigit())
            for item in raw.split(",")
            if item.strip()
        }

    def missing_analysis_credentials(self) -> list[str]:
        missing: list[str] = []
        if not self.aletheia_configured:
            missing.append("DETECTIF_API_KEY")
        if not self.gemini_configured:
            missing.append("GEMINI_API_KEY")
        return missing


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached Settings instance."""
    return Settings()
