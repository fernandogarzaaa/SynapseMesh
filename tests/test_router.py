"""Tests for the semantic model router."""

from __future__ import annotations

import pytest

from app.catalog import ModelProfile
from app.router import NoEligibleModelError, RoutingRequest, SemanticRouter

CODE_MODEL = ModelProfile(
    provider="anthropic",
    model="claude-code-specialist",
    context_window=200_000,
    supports_vision=False,
    supports_tool_use=True,
    cost_tier="medium",
    latency_tier="balanced",
    strengths=["code", "reasoning"],
)

VISION_MODEL = ModelProfile(
    provider="openai",
    model="gpt-vision",
    context_window=128_000,
    supports_vision=True,
    supports_tool_use=True,
    cost_tier="high",
    latency_tier="balanced",
    strengths=["vision", "chat"],
)

CHAT_FAST_MODEL = ModelProfile(
    provider="anthropic",
    model="chat-fast",
    context_window=100_000,
    supports_vision=False,
    supports_tool_use=True,
    cost_tier="low",
    latency_tier="fast",
    strengths=["chat"],
)

CHAT_QUALITY_MODEL = ModelProfile(
    provider="anthropic",
    model="chat-quality",
    context_window=100_000,
    supports_vision=False,
    supports_tool_use=True,
    cost_tier="low",
    latency_tier="slow",
    strengths=["chat"],
)

LONG_CONTEXT_MODEL = ModelProfile(
    provider="google",
    model="gemini-long",
    context_window=1_000_000,
    supports_vision=True,
    supports_tool_use=True,
    cost_tier="high",
    latency_tier="slow",
    strengths=["long_context"],
)

BASE_CATALOG = [CODE_MODEL, VISION_MODEL, CHAT_FAST_MODEL, CHAT_QUALITY_MODEL, LONG_CONTEXT_MODEL]


def test_hard_filter_rejects_insufficient_context_window() -> None:
    router = SemanticRouter()
    request = RoutingRequest(
        task_description="summarize this",
        required_context_tokens=5_000_000,
    )

    with pytest.raises(NoEligibleModelError):
        router.select(request, BASE_CATALOG)


def test_hard_filter_rejects_missing_vision_support() -> None:
    router = SemanticRouter()
    catalog = [CODE_MODEL, CHAT_FAST_MODEL]
    request = RoutingRequest(task_description="describe this screenshot", requires_vision=True)

    with pytest.raises(NoEligibleModelError):
        router.select(request, catalog)


def test_code_task_routes_to_code_strength_model() -> None:
    router = SemanticRouter()
    request = RoutingRequest(task_description="please debug this function and fix the bug")

    decision = router.select(request, BASE_CATALOG)

    assert decision.selected.model == CODE_MODEL.model
    assert "code" in decision.matched_strengths


def test_vision_task_requires_vision_capable_model() -> None:
    router = SemanticRouter()
    request = RoutingRequest(
        task_description="what is in this screenshot image",
        requires_vision=True,
    )

    decision = router.select(request, BASE_CATALOG)

    assert decision.selected.supports_vision is True
    assert decision.selected.model == VISION_MODEL.model
    assert "vision" in decision.matched_strengths


def test_latency_preference_changes_pick_between_tied_candidates() -> None:
    router = SemanticRouter()
    catalog = [CHAT_FAST_MODEL, CHAT_QUALITY_MODEL]
    task = "just chat with me"

    fast_decision = router.select(
        RoutingRequest(task_description=task, latency_preference="fast"),
        catalog,
    )
    quality_decision = router.select(
        RoutingRequest(task_description=task, latency_preference="quality"),
        catalog,
    )

    assert fast_decision.selected.model == CHAT_FAST_MODEL.model
    assert quality_decision.selected.model == CHAT_QUALITY_MODEL.model


def test_fallback_chain_is_ranked_not_arbitrary() -> None:
    router = SemanticRouter()
    request = RoutingRequest(task_description="please debug this function")

    decision = router.select(request, BASE_CATALOG)

    scores = [
        router._score(profile, decision.matched_strengths, request)
        for profile in [decision.selected, *decision.fallback_chain]
    ]

    assert scores == sorted(scores, reverse=True)
    assert decision.selected not in decision.fallback_chain
    assert len(decision.fallback_chain) <= 3
