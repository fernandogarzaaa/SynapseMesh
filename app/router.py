"""Predictive semantic router for the 2026 model grid."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import ClassVar

logger = logging.getLogger("synapse_mesh.router")


@dataclass(frozen=True)
class ModelProfile:
    """Metadata for a routed model."""

    name: str
    capability: str
    provider: str


class PredictiveRouter:
    """Assign actor and critic roles from semantic task complexity."""

    registry: ClassVar[dict[str, ModelProfile]] = {
        "gpt-5.3-codex": ModelProfile(
            name="gpt-5.3-codex",
            capability="Low-Latency Code Synthesis",
            provider="openai",
        ),
        "gpt-5.5": ModelProfile(
            name="gpt-5.5",
            capability="General Task Ingress",
            provider="openai",
        ),
        "claude-mythos-5": ModelProfile(
            name="claude-mythos-5",
            capability="High-Tier Code Integration",
            provider="anthropic",
        ),
        "claude-fable-5": ModelProfile(
            name="claude-fable-5",
            capability="Deep Reasoning and System Automations",
            provider="anthropic",
        ),
    }

    async def assign_roles(self, task_description: str) -> tuple[str, str]:
        """Choose actor and critic models for the supplied task."""

        text = task_description.lower()
        deep_signals = (
            "architecture",
            "distributed",
            "terminal-bench",
            "benchmark",
            "security",
            "compliance",
            "orchestration",
            "system automation",
            "fault tolerant",
            "deep reasoning",
            "multi-model",
        )
        fast_signals = (
            "format",
            "standard",
            "simple",
            "raw generation",
            "high-speed",
            "low latency",
            "template",
        )
        deep_score = sum(1 for signal in deep_signals if signal in text)
        fast_score = sum(1 for signal in fast_signals if signal in text)

        if deep_score > fast_score:
            actor, critic = "claude-mythos-5", "claude-fable-5"
        else:
            actor, critic = "gpt-5.3-codex", "gpt-5.5"

        logger.info(
            "roles_assigned",
            extra={
                "actor_model": actor,
                "critic_model": critic,
                "deep_score": deep_score,
                "fast_score": fast_score,
            },
        )
        return actor, critic

    def provider_for(self, model_name: str) -> str:
        """Return the provider key for a registered model."""

        return self.registry[model_name].provider
