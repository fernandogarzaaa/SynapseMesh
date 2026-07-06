"""Redis Streams based asynchronous pub/sub broker for SwarmBus."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator, Mapping
from typing import Any, TypeAlias, cast

from redis.asyncio import Redis
from redis.exceptions import RedisError, ResponseError

logger = logging.getLogger(__name__)

RedisScalar: TypeAlias = bytes | bytearray | memoryview | str | int | float


class BrokerError(RuntimeError):
    """Raised when Redis stream operations fail."""


class StreamBroker:
    """Thin, typed wrapper around Redis Streams consumer-group operations."""

    def __init__(
        self,
        redis_client: Redis,
        *,
        stream_prefix: str = "swarmbus",
        read_count: int = 10,
        block_ms: int = 5_000,
    ) -> None:
        self._redis = redis_client
        self._stream_prefix = stream_prefix.strip(":")
        self._read_count = read_count
        self._block_ms = block_ms

    def _stream_name(self, topic: str) -> str:
        normalized_topic = topic.strip()
        if not normalized_topic:
            raise ValueError("topic must not be empty")
        return f"{self._stream_prefix}:{normalized_topic}"

    @staticmethod
    def _encode_payload(message: Mapping[str, Any]) -> dict[RedisScalar, RedisScalar]:
        encoded: dict[RedisScalar, RedisScalar] = {}
        for key, value in message.items():
            encoded[str(key)] = json.dumps(value, separators=(",", ":"), sort_keys=True)
        return encoded

    @staticmethod
    def _decode_value(value: Any) -> Any:
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        if not isinstance(value, str):
            return value
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value

    @classmethod
    def _decode_payload(cls, event_id: str, payload: Mapping[Any, Any]) -> dict[str, Any]:
        decoded: dict[str, Any] = {"event_id": event_id}
        for key, value in payload.items():
            decoded_key = key.decode("utf-8") if isinstance(key, bytes) else str(key)
            decoded[decoded_key] = cls._decode_value(value)
        return decoded

    async def publish_event(self, topic: str, message: dict[str, Any]) -> str:
        """Append an event payload to a Redis Stream topic."""

        stream = self._stream_name(topic)
        if not message:
            raise ValueError("message must not be empty")

        logger.info(
            "swarm_bus_publish_requested",
            extra={"topic": topic, "stream": stream, "field_count": len(message)},
        )
        try:
            raw_event_id = await self._redis.xadd(stream, self._encode_payload(message))
        except RedisError as exc:
            logger.exception(
                "swarm_bus_publish_failed",
                extra={"topic": topic, "stream": stream, "error_type": type(exc).__name__},
            )
            raise BrokerError(f"failed to publish event to topic {topic!r}") from exc

        event_id = (
            raw_event_id.decode("utf-8")
            if isinstance(raw_event_id, bytes)
            else str(raw_event_id)
        )
        logger.info("swarm_bus_publish_committed", extra={"topic": topic, "event_id": event_id})
        return event_id

    async def _ensure_consumer_group(self, stream: str, consumer_group: str) -> None:
        try:
            await self._redis.xgroup_create(stream, consumer_group, id="0", mkstream=True)
            logger.info(
                "swarm_bus_consumer_group_created",
                extra={"stream": stream, "consumer_group": consumer_group},
            )
        except ResponseError as exc:
            message = str(exc).lower()
            if "busygroup" in message:
                logger.debug(
                    "swarm_bus_consumer_group_exists",
                    extra={"stream": stream, "consumer_group": consumer_group},
                )
                return
            logger.exception(
                "swarm_bus_consumer_group_create_failed",
                extra={"stream": stream, "consumer_group": consumer_group},
            )
            raise BrokerError(f"failed to create consumer group {consumer_group!r}") from exc
        except RedisError as exc:
            logger.exception(
                "swarm_bus_consumer_group_create_failed",
                extra={"stream": stream, "consumer_group": consumer_group},
            )
            raise BrokerError(f"failed to create consumer group {consumer_group!r}") from exc

    async def subscribe_topic(
        self,
        topic: str,
        consumer_group: str,
        consumer_name: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Yield events from a Redis Stream consumer group and acknowledge successful yields."""

        if not consumer_group.strip():
            raise ValueError("consumer_group must not be empty")
        if not consumer_name.strip():
            raise ValueError("consumer_name must not be empty")

        stream = self._stream_name(topic)
        await self._ensure_consumer_group(stream, consumer_group)
        logger.info(
            "swarm_bus_subscription_started",
            extra={"topic": topic, "stream": stream, "consumer_group": consumer_group},
        )

        while True:
            try:
                packets = await self._redis.xreadgroup(
                    consumer_group,
                    consumer_name,
                    streams={stream: ">"},
                    count=self._read_count,
                    block=self._block_ms,
                )
            except RedisError as exc:
                logger.exception(
                    "swarm_bus_subscription_read_failed",
                    extra={"topic": topic, "stream": stream, "consumer_group": consumer_group},
                )
                raise BrokerError(f"failed to read from topic {topic!r}") from exc

            typed_packets = cast(list[tuple[Any, list[tuple[Any, Mapping[Any, Any]]]]], packets)
            if not typed_packets:
                logger.debug(
                    "swarm_bus_subscription_idle",
                    extra={"topic": topic, "stream": stream},
                )
                continue

            for _stream_key, events in typed_packets:
                for raw_event_id, payload in events:
                    event_id = (
                        raw_event_id.decode("utf-8")
                        if isinstance(raw_event_id, bytes)
                        else str(raw_event_id)
                    )
                    event = self._decode_payload(event_id, payload)
                    yield event
                    try:
                        await self._redis.xack(stream, consumer_group, event_id)
                    except RedisError as exc:
                        logger.exception(
                            "swarm_bus_subscription_ack_failed",
                            extra={
                                "topic": topic,
                                "stream": stream,
                                "consumer_group": consumer_group,
                                "event_id": event_id,
                            },
                        )
                        raise BrokerError(f"failed to acknowledge event {event_id!r}") from exc
                    logger.debug(
                        "swarm_bus_subscription_acknowledged",
                        extra={"topic": topic, "event_id": event_id},
                    )
