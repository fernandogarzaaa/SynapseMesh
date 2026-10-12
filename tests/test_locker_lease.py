"""Lease renew/release semantics for DistributedAgentLocker."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from redis.exceptions import RedisError

from app.locker import DistributedAgentLocker, LockError


@pytest.mark.asyncio
async def test_renew_extends_ttl_only_for_owner() -> None:
    redis_client = AsyncMock()
    redis_client.eval = AsyncMock(side_effect=[1, 0])
    locker = DistributedAgentLocker(redis_client)

    assert await locker.renew_task_lock("task-7", "agent-alpha") is True
    assert await locker.renew_task_lock("task-7", "agent-beta") is False

    script, nkeys, key, owner, ttl = redis_client.eval.await_args_list[0].args
    assert "PEXPIRE" in script and "GET" in script
    assert (nkeys, key, owner, ttl) == (1, "swarmbus:lock:task-7", "agent-alpha", "10000")


@pytest.mark.asyncio
async def test_renew_accepts_custom_ttl_and_rejects_bad_input() -> None:
    redis_client = AsyncMock()
    redis_client.eval = AsyncMock(return_value=1)
    locker = DistributedAgentLocker(redis_client)

    assert await locker.renew_task_lock("t", "a", ttl_ms=30_000) is True
    assert redis_client.eval.await_args.args[-1] == "30000"
    with pytest.raises(ValueError):
        await locker.renew_task_lock("t", "a", ttl_ms=0)
    with pytest.raises(ValueError):
        await locker.renew_task_lock("t", "  ")
    with pytest.raises(ValueError):
        await locker.renew_task_lock(" ", "a")


@pytest.mark.asyncio
async def test_renew_wraps_redis_errors() -> None:
    redis_client = AsyncMock()
    redis_client.eval = AsyncMock(side_effect=RedisError("down"))
    locker = DistributedAgentLocker(redis_client)
    with pytest.raises(LockError):
        await locker.renew_task_lock("t", "a")


@pytest.mark.asyncio
async def test_release_reports_whether_lock_was_owned() -> None:
    redis_client = AsyncMock()
    redis_client.eval = AsyncMock(side_effect=[1, 0])
    locker = DistributedAgentLocker(redis_client)

    assert await locker.release_task_lock("task-7", "agent-alpha") is True
    assert await locker.release_task_lock("task-7", "agent-beta") is False
    script = redis_client.eval.await_args_list[0].args[0]
    assert "DEL" in script



@pytest.mark.asyncio
async def test_renew_and_release_endpoints() -> None:
    from fastapi import HTTPException

    from app.main import ClaimRequest, release_task, renew_task

    locker = AsyncMock()
    locker.renew_task_lock = AsyncMock(return_value=True)
    locker.release_task_lock = AsyncMock(return_value=False)
    body = ClaimRequest(task_id="task-7", agent_id="agent-alpha")

    renewed = await renew_task(body, locker)
    assert (renewed.task_id, renewed.agent_id, renewed.ok) == ("task-7", "agent-alpha", True)
    released = await release_task(body, locker)
    assert released.ok is False

    locker.renew_task_lock = AsyncMock(side_effect=LockError("down"))
    with pytest.raises(HTTPException) as excinfo:
        await renew_task(body, locker)
    assert excinfo.value.status_code == 503


@pytest.mark.asyncio
async def test_release_passes_one_key_and_validates_input() -> None:
    redis_client = AsyncMock()
    redis_client.eval = AsyncMock(return_value=1)
    locker = DistributedAgentLocker(redis_client)

    await locker.release_task_lock("task-7", "agent-alpha")
    assert redis_client.eval.await_args.args[1:] == (1, "swarmbus:lock:task-7", "agent-alpha")
    with pytest.raises(ValueError):
        await locker.release_task_lock("task-7", "   ")


@pytest.mark.asyncio
async def test_release_wraps_redis_errors() -> None:
    redis_client = AsyncMock()
    redis_client.eval = AsyncMock(side_effect=RedisError("down"))
    locker = DistributedAgentLocker(redis_client)
    with pytest.raises(LockError):
        await locker.release_task_lock("t", "a")
