"""Distributed task locking for competing SwarmBus agents."""

from __future__ import annotations

import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config import settings

logger = logging.getLogger(__name__)


class LockError(RuntimeError):
    """Raised when lock operations cannot be completed safely."""


class DistributedAgentLocker:
    """Redis-backed lease manager for agent task ownership."""

    _RELEASE_SCRIPT = """
    if redis.call("GET", KEYS[1]) == ARGV[1] then
        return redis.call("DEL", KEYS[1])
    end
    return 0
    """

    def __init__(self, redis_client: Redis, *, key_prefix: str = "swarmbus:lock") -> None:
        self._redis = redis_client
        self._key_prefix = key_prefix.strip(":")

    def _lock_key(self, task_id: str) -> str:
        normalized_task = task_id.strip()
        if not normalized_task:
            raise ValueError("task_id must not be empty")
        return f"{self._key_prefix}:{normalized_task}"

    async def acquire_task_lock(self, task_id: str, agent_id: str) -> bool:
        """Acquire a unique Redis lease for a task when no competing owner exists."""

        normalized_agent = agent_id.strip()
        if not normalized_agent:
            raise ValueError("agent_id must not be empty")

        lock_key = self._lock_key(task_id)
        try:
            acquired = await self._redis.set(
                lock_key,
                normalized_agent,
                nx=True,
                px=settings.LOCK_TTL_MS,
            )
        except RedisError as exc:
            logger.exception(
                "swarm_bus_lock_acquire_failed",
                extra={"task_id": task_id, "agent_id": normalized_agent},
            )
            raise LockError(f"failed to acquire lock for task {task_id!r}") from exc

        result = bool(acquired)
        logger.info(
            "swarm_bus_lock_acquire_result",
            extra={"task_id": task_id, "agent_id": normalized_agent, "acquired": result},
        )
        return result

    async def release_task_lock(self, task_id: str, agent_id: str) -> None:
        """Release a task lease only when the requesting agent owns the Redis key."""

        normalized_agent = agent_id.strip()
        if not normalized_agent:
            raise ValueError("agent_id must not be empty")

        lock_key = self._lock_key(task_id)
        try:
            released = await self._redis.eval(self._RELEASE_SCRIPT, 1, lock_key, normalized_agent)
        except RedisError as exc:
            logger.exception(
                "swarm_bus_lock_release_failed",
                extra={"task_id": task_id, "agent_id": normalized_agent},
            )
            raise LockError(f"failed to release lock for task {task_id!r}") from exc

        logger.info(
            "swarm_bus_lock_release_result",
            extra={
                "task_id": task_id,
                "agent_id": normalized_agent,
                "released": bool(int(released)),
            },
        )

