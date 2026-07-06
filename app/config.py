"""Runtime configuration for the SwarmBus coordination fabric."""

from __future__ import annotations

import logging

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Environment-backed SwarmBus settings."""

    REDIS_URL: str = Field(default="redis://localhost:6379/0")
    MAX_LOOP_DEPTH: int = Field(default=4, ge=1, le=128)
    LOCK_TTL_MS: int = Field(default=10_000, ge=100, le=86_400_000)
    AGENT_BUS_ENV: str = Field(default="production", min_length=1)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )


try:
    settings = Settings()
except ValidationError:
    logger.exception("swarm_bus_settings_validation_failed")
    raise

