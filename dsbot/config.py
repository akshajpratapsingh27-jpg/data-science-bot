"""Configuration loading for dsbot.

Configuration is read from ``config/settings.yaml`` and then overridden by
the runtime environment where appropriate. Secrets are intentionally kept in
environment variables rather than the sample YAML file.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

DEFAULT_SETTINGS_PATH = Path("config/settings.yaml")
DEFAULT_AGENT_MODEL = "claude-3-5-sonnet-20241022"


class Settings(BaseModel):
    """Runtime settings used by the core services."""

    agent_model: str = DEFAULT_AGENT_MODEL
    anthropic_api_key: str | None = None
    anthropic_base_url: str | None = None
    mode: str = "mock"
    vip_senders: list[str] = Field(default_factory=list)
    allowlisted_domains: list[str] = Field(default_factory=list)
    working_hours: dict[str, str] = Field(
        default_factory=lambda: {"start": "09:00", "end": "17:00"}
    )
    timezone: str = "UTC"
    autonomy_level: str = "L0"
    meeting_buffer_minutes: int = 15
    llm_requests_per_minute: int = Field(default=60, ge=1)
    llm_daily_token_cap: int = Field(default=100_000, ge=0)
    llm_daily_cost_cap_usd: float = Field(default=5.0, ge=0)
    llm_cost_per_million_tokens_usd: float = Field(default=3.0, ge=0)
    llm_max_retries: int = Field(default=2, ge=0, le=10)
    llm_backoff_base_seconds: float = Field(default=0.5, ge=0)
    meeting_priority_tiers: dict[str, int] = Field(
        default_factory=lambda: {
            "optional": 1,
            "internal_sync": 2,
            "manager": 3,
            "client": 4,
        }
    )
    triage_weights: dict[str, int] = Field(
        default_factory=lambda: {
            "vip_sender": 40,
            "deadline_within_48h": 25,
            "urgent_action": 20,
            "boss_client_keyword": 10,
            "thread_activity": 5,
        }
    )
    boss_client_keywords: list[str] = Field(
        default_factory=lambda: ["boss", "client", "customer", "director", "executive"]
    )

    @property
    def api_key(self) -> str | None:
        """Compatibility alias for callers that use the shorter name."""

        return self.anthropic_api_key


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}

    with path.open("r", encoding="utf-8") as settings_file:
        values = yaml.safe_load(settings_file) or {}

    if not isinstance(values, dict):
        raise ValueError(f"Settings file must contain a mapping: {path}")
    return values


def load_settings(path: Path | str = DEFAULT_SETTINGS_PATH) -> Settings:
    """Load YAML settings and apply supported environment overrides."""

    values = _read_yaml(Path(path))
    values.pop("anthropic_api_key", None)
    values.pop("api_key", None)

    environment_overrides = {
        "agent_model": os.getenv("ANTHROPIC_MODEL") or os.getenv("AGENT_MODEL"),
        "anthropic_api_key": (
            os.getenv("OPENROUTER_API_KEY")
            or os.getenv("ANTHROPIC_AUTH_TOKEN")
            or os.getenv("ANTHROPIC_API_KEY")
        ),
        "anthropic_base_url": os.getenv("ANTHROPIC_BASE_URL"),
        "mode": os.getenv("DSBOT_MODE"),
        "llm_requests_per_minute": os.getenv("DSBOT_LLM_REQUESTS_PER_MINUTE"),
        "llm_daily_token_cap": os.getenv("DSBOT_LLM_DAILY_TOKEN_CAP"),
        "llm_daily_cost_cap_usd": os.getenv("DSBOT_LLM_DAILY_COST_CAP_USD"),
        "llm_cost_per_million_tokens_usd": os.getenv(
            "DSBOT_LLM_COST_PER_MILLION_TOKENS_USD"
        ),
        "llm_max_retries": os.getenv("DSBOT_LLM_MAX_RETRIES"),
        "llm_backoff_base_seconds": os.getenv("DSBOT_LLM_BACKOFF_BASE_SECONDS"),
    }
    for key, value in environment_overrides.items():
        if value is not None:
            values[key] = value

    values.setdefault("agent_model", DEFAULT_AGENT_MODEL)
    values.setdefault("mode", "mock")
    return Settings.model_validate(values)


def get_settings(path: Path | str = DEFAULT_SETTINGS_PATH) -> Settings:
    """Return the current settings without caching environment-sensitive data."""

    return load_settings(path)
