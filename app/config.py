"""Environment-driven settings for SynapseMesh."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Global runtime configuration parsed from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/synapse_mesh",
        description="Async SQLAlchemy database URL.",
    )
    OPENAI_API_URL: str = Field(
        default="https://api.openai.com/v1/responses",
        description="Public OpenAI-compatible model ingress endpoint.",
    )
    ANTHROPIC_API_URL: str = Field(
        default="https://api.anthropic.com/v1/messages",
        description="Public Anthropic-compatible model ingress endpoint.",
    )
    MAX_CONCURRENT_STREAM_LOOPS: int = Field(default=5, ge=1, le=50)
    COMPLIANCE_MODE: str = Field(default="STRICT")
    INTERNAL_OPENAI_API_URL: str = Field(
        default="http://internal-model-gateway.local/openai",
        description="Internal OpenAI-compatible endpoint used for restricted data.",
    )
    INTERNAL_ANTHROPIC_API_URL: str = Field(
        default="http://internal-model-gateway.local/anthropic",
        description="Internal Anthropic-compatible endpoint used for restricted data.",
    )
    PROVIDER_TIMEOUT_SECONDS: float = Field(default=30.0, gt=0.0)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings."""

    return Settings()


settings = get_settings()
