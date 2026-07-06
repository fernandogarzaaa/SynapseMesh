"""Distributed integration tests for SwarmBus coordination primitives."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.detector import DeadlockLoopInterceptor
from app.locker import DistributedAgentLocker


@pytest.mark.asyncio
async def test_race_condition_defense_allows_single_task_owner() -> None:
    redis_client = AsyncMock()
    redis_client.set = AsyncMock(side_effect=[True, None])
    locker = DistributedAgentLocker(redis_client)

    first_result, second_result = await asyncio.gather(
        locker.acquire_task_lock("task-42", "agent-alpha"),
        locker.acquire_task_lock("task-42", "agent-beta"),
    )

    assert first_result is True
    assert second_result is False
    assert redis_client.set.await_count == 2
    redis_client.set.assert_any_await(
        "swarmbus:lock:task-42",
        "agent-alpha",
        nx=True,
        px=10_000,
    )
    redis_client.set.assert_any_await(
        "swarmbus:lock:task-42",
        "agent-beta",
        nx=True,
        px=10_000,
    )


def test_loop_interception_rejects_cyclic_unmodified_handoff() -> None:
    detector = DeadlockLoopInterceptor(max_loop_depth=4)
    cyclic_history = [
        {"from": "planner", "to": "coder", "payload": {"asset": "spec-v1"}},
        {"from": "coder", "to": "planner", "payload": {"asset": "spec-v1"}},
    ]

    assert detector.validate_trajectory(cyclic_history) is False

