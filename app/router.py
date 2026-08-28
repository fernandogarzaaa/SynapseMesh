"""Semantic model routing.

Given a task description and constraints, decide which model/provider is
the right fit and return a ranked decision. This module makes no network
or live LLM calls -- it is pure, deterministic scoring logic over a static
:mod:`app.catalog` of model capability profiles. Downstream services (e.g.
async-mcp-gateway) are responsible for actually executing calls and
handling live provider failover; this router only decides *what* to route
to and hands back a ranked fallback chain for that gateway to walk.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.catalog import COST_TIER_ORDER, CostTier, ModelProfile

LatencyPreference = Literal["fast", "balanced", "quality"]

# Keyword sets per strength category. Order matters only for reasoning
# text; scoring itself considers every category independently.
_STRENGTH_KEYWORDS: dict[str, set[str]] = {
    "code": {
        "code",
        "coding",
        "debug",
        "debugging",
        "function",
        "bug",
        "refactor",
        "compile",
        "unit test",
        "stack trace",
        "script",
    },
    "vision": {
        "image",
        "photo",
        "picture",
        "see",
        "screenshot",
        "diagram",
        "visual",
        "chart",
        "video frame",
    },
    "long_context": {
        "long",
        "entire document",
        "full codebase",
        "whole repository",
        "large document",
        "lengthy",
        "book",
        "transcript",
    },
    "reasoning": {
        "reason",
        "reasoning",
        "think",
        "solve",
        "prove",
        "proof",
        "logic",
        "puzzle",
        "strategy",
        "plan",
    },
}

DEFAULT_CATEGORY = "chat"


class NoEligibleModelError(RuntimeError):
    """Raised when no catalog entry survives the hard filter."""


class RoutingRequest(BaseModel):
    """Constraints and description of the task to be routed."""

    task_description: str = Field(min_length=1)
    required_context_tokens: int = 0
    requires_vision: bool = False
    requires_tool_use: bool = False
    latency_preference: LatencyPreference = "balanced"
    max_cost_tier: CostTier = "high"


class RoutingDecision(BaseModel):
    """Result of a semantic routing decision."""

    selected: ModelProfile
    fallback_chain: list[ModelProfile]
    reasoning: str
    matched_strengths: list[str]


def classify_task(task_description: str) -> list[str]:
    """Classify a task description against known strength categories.

    Returns every matched category, based on simple keyword membership.
    Falls back to ``["chat"]`` when nothing matches.
    """

    lowered = task_description.lower()
    matched = [
        category
        for category, keywords in _STRENGTH_KEYWORDS.items()
        if any(keyword in lowered for keyword in keywords)
    ]
    return matched or [DEFAULT_CATEGORY]


_LATENCY_BONUS: dict[LatencyPreference, dict[str, float]] = {
    "fast": {"fast": 1.5, "balanced": 0.5, "slow": -1.0},
    "balanced": {"fast": 0.5, "balanced": 1.0, "slow": 0.0},
    "quality": {"fast": -0.5, "balanced": 0.5, "slow": 1.0},
}

# Small per-tier penalty so cost acts as a tiebreaker rather than a
# dominant factor: strength matches (worth 2.0 each) always outrank it.
_COST_PENALTY: dict[CostTier, float] = {"low": 0.0, "medium": -0.25, "high": -0.5}


class SemanticRouter:
    """Deterministic, keyword-driven router over a static model catalog."""

    def select(self, request: RoutingRequest, catalog: list[ModelProfile]) -> RoutingDecision:
        eligible = self._hard_filter(request, catalog)
        if not eligible:
            raise NoEligibleModelError(
                "no catalog model satisfies the requested constraints "
                f"(min_context={request.required_context_tokens}, "
                f"requires_vision={request.requires_vision}, "
                f"requires_tool_use={request.requires_tool_use}, "
                f"max_cost_tier={request.max_cost_tier!r})"
            )

        matched_strengths = classify_task(request.task_description)

        scored = sorted(
            eligible,
            key=lambda profile: self._score(profile, matched_strengths, request),
            reverse=True,
        )

        selected = scored[0]
        fallback_chain = scored[1:4]
        reasoning = self._explain(selected, scored, matched_strengths, request)

        return RoutingDecision(
            selected=selected,
            fallback_chain=fallback_chain,
            reasoning=reasoning,
            matched_strengths=matched_strengths,
        )

    @staticmethod
    def _hard_filter(request: RoutingRequest, catalog: list[ModelProfile]) -> list[ModelProfile]:
        max_cost_rank = COST_TIER_ORDER[request.max_cost_tier]
        eligible = []
        for profile in catalog:
            if profile.context_window < request.required_context_tokens:
                continue
            if request.requires_vision and not profile.supports_vision:
                continue
            if request.requires_tool_use and not profile.supports_tool_use:
                continue
            if COST_TIER_ORDER[profile.cost_tier] > max_cost_rank:
                continue
            eligible.append(profile)
        return eligible

    @staticmethod
    def _score(
        profile: ModelProfile,
        matched_strengths: list[str],
        request: RoutingRequest,
    ) -> float:
        strength_score = 2.0 * sum(1 for s in matched_strengths if s in profile.strengths)
        latency_score = _LATENCY_BONUS[request.latency_preference][profile.latency_tier]
        cost_score = _COST_PENALTY[profile.cost_tier]
        return strength_score + latency_score + cost_score

    def _explain(
        self,
        selected: ModelProfile,
        ranked: list[ModelProfile],
        matched_strengths: list[str],
        request: RoutingRequest,
    ) -> str:
        matched_on_selected = [s for s in matched_strengths if s in selected.strengths]
        categories = ", ".join(matched_strengths)
        strengths_text = (
            f"matches {', '.join(matched_on_selected)}"
            if matched_on_selected
            else "matched no strength category directly, chosen on latency/cost tiebreak"
        )

        base = (
            f"Task classified as [{categories}]; selected {selected.provider}/{selected.model} "
            f"({strengths_text}, latency={selected.latency_tier}, cost={selected.cost_tier}) "
            f"under latency_preference={request.latency_preference!r}."
        )

        if len(ranked) > 1:
            runner_up = ranked[1]
            selected_score = self._score(selected, matched_strengths, request)
            runner_up_score = self._score(runner_up, matched_strengths, request)
            base += (
                f" Beat runner-up {runner_up.provider}/{runner_up.model} "
                f"({selected_score:.2f} vs {runner_up_score:.2f})."
            )

        return base
