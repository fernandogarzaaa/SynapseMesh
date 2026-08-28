"""Model catalog definitions for the semantic model router.

The catalog is a static, data-driven list of :class:`ModelProfile` entries
describing the capability envelope of each candidate model/provider pair.
No network calls or live provider queries are involved -- the catalog is
either the built-in default below or an operator-supplied JSON override
loaded from ``MODEL_CATALOG_PATH``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.config import settings

logger = logging.getLogger(__name__)

CostTier = Literal["low", "medium", "high"]
LatencyTier = Literal["fast", "balanced", "slow"]

COST_TIER_ORDER: dict[CostTier, int] = {"low": 0, "medium": 1, "high": 2}


class ModelProfile(BaseModel):
    """Capability profile for a single routable model."""

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    context_window: int = Field(ge=0)
    supports_vision: bool = False
    supports_tool_use: bool = False
    cost_tier: CostTier
    latency_tier: LatencyTier
    strengths: list[str] = Field(default_factory=list)


DEFAULT_CATALOG: list[ModelProfile] = [
    ModelProfile(
        provider="anthropic",
        model="claude-small-fast",
        context_window=200_000,
        supports_vision=True,
        supports_tool_use=True,
        cost_tier="low",
        latency_tier="fast",
        strengths=["chat", "code"],
    ),
    ModelProfile(
        provider="anthropic",
        model="claude-mid-balanced",
        context_window=200_000,
        supports_vision=True,
        supports_tool_use=True,
        cost_tier="medium",
        latency_tier="balanced",
        strengths=["code", "reasoning", "chat"],
    ),
    ModelProfile(
        provider="anthropic",
        model="claude-large-frontier",
        context_window=200_000,
        supports_vision=True,
        supports_tool_use=True,
        cost_tier="high",
        latency_tier="slow",
        strengths=["reasoning", "code", "long_context"],
    ),
    ModelProfile(
        provider="openai",
        model="gpt-small-fast",
        context_window=128_000,
        supports_vision=False,
        supports_tool_use=True,
        cost_tier="low",
        latency_tier="fast",
        strengths=["chat"],
    ),
    ModelProfile(
        provider="openai",
        model="gpt-large-frontier",
        context_window=128_000,
        supports_vision=True,
        supports_tool_use=True,
        cost_tier="high",
        latency_tier="balanced",
        strengths=["reasoning", "code", "vision"],
    ),
    ModelProfile(
        provider="google",
        model="gemini-mid-balanced",
        context_window=1_000_000,
        supports_vision=True,
        supports_tool_use=True,
        cost_tier="medium",
        latency_tier="balanced",
        strengths=["long_context", "vision", "chat"],
    ),
    ModelProfile(
        provider="google",
        model="gemini-large-longcontext",
        context_window=2_000_000,
        supports_vision=True,
        supports_tool_use=True,
        cost_tier="high",
        latency_tier="slow",
        strengths=["long_context", "reasoning", "vision"],
    ),
]


def _load_catalog_from_path(path: str) -> list[ModelProfile] | None:
    catalog_path = Path(path)
    if not catalog_path.is_file():
        logger.warning(
            "semantic_router_catalog_override_missing",
            extra={"path": path},
        )
        return None

    try:
        raw = json.loads(catalog_path.read_text(encoding="utf-8"))
        return [ModelProfile.model_validate(entry) for entry in raw]
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        logger.exception(
            "semantic_router_catalog_override_invalid",
            extra={"path": path},
        )
        return None


def load_catalog() -> list[ModelProfile]:
    """Return the active model catalog.

    Loads an override from ``MODEL_CATALOG_PATH`` when set and valid,
    otherwise falls back to :data:`DEFAULT_CATALOG`.
    """

    override_path = settings.MODEL_CATALOG_PATH
    if override_path:
        override = _load_catalog_from_path(override_path)
        if override:
            return override

    return list(DEFAULT_CATALOG)
