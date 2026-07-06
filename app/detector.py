"""Graph-based handoff loop detection for SwarmBus orchestration safety."""

from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from app.config import settings

logger = logging.getLogger(__name__)

VisitState = Literal["visiting", "visited"]


class DeadlockLoopInterceptor:
    """Validates agent handoff traces before they enter the live bus."""

    def __init__(self, *, max_loop_depth: int | None = None) -> None:
        self._max_loop_depth = (
            max_loop_depth if max_loop_depth is not None else settings.MAX_LOOP_DEPTH
        )

    @staticmethod
    def _payload_fingerprint(event: Mapping[str, Any]) -> str:
        payload = event.get("payload", event.get("asset", event.get("work_asset", {})))
        normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _coerce_node(value: Any, *, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"execution history event field {field!r} must be a non-empty string")
        return value.strip()

    def _build_graph(
        self,
        execution_history: Iterable[Mapping[str, Any]],
    ) -> tuple[dict[str, set[str]], dict[tuple[str, str], set[str]], int]:
        graph: dict[str, set[str]] = defaultdict(set)
        edge_payloads: dict[tuple[str, str], set[str]] = defaultdict(set)
        event_count = 0

        for event in execution_history:
            source = self._coerce_node(event.get("from"), field="from")
            destination = self._coerce_node(event.get("to"), field="to")
            fingerprint = self._payload_fingerprint(event)
            graph[source].add(destination)
            graph.setdefault(destination, set())
            edge_payloads[(source, destination)].add(fingerprint)
            event_count += 1

        return dict(graph), dict(edge_payloads), event_count

    def _has_cycle(self, graph: Mapping[str, set[str]]) -> bool:
        states: dict[str, VisitState] = {}

        def visit(node: str, depth: int) -> bool:
            if depth > self._max_loop_depth:
                logger.critical(
                    "swarm_bus_loop_depth_exceeded",
                    extra={"node": node, "depth": depth, "max_loop_depth": self._max_loop_depth},
                )
                return True
            state = states.get(node)
            if state == "visiting":
                return True
            if state == "visited":
                return False

            states[node] = "visiting"
            for neighbor in graph.get(node, set()):
                if visit(neighbor, depth + 1):
                    return True
            states[node] = "visited"
            return False

        return any(states.get(node) is None and visit(node, 1) for node in graph)

    @staticmethod
    def _has_unmodified_repeat(edge_payloads: Mapping[tuple[str, str], set[str]]) -> bool:
        observed: set[tuple[str, str, str]] = set()
        for (source, destination), payloads in edge_payloads.items():
            for payload_hash in payloads:
                edge_state = (source, destination, payload_hash)
                if edge_state in observed:
                    return True
                observed.add(edge_state)
        return False

    @staticmethod
    def _history_has_duplicate_edge_payload(
        execution_history: Iterable[Mapping[str, Any]],
    ) -> bool:
        observed: set[tuple[str, str, str]] = set()
        for event in execution_history:
            source = str(event.get("from", "")).strip()
            destination = str(event.get("to", "")).strip()
            payload_hash = DeadlockLoopInterceptor._payload_fingerprint(event)
            edge_state = (source, destination, payload_hash)
            if edge_state in observed:
                return True
            observed.add(edge_state)
        return False

    def validate_trajectory(self, execution_history: list[dict[str, Any]]) -> bool:
        """Return False when a cyclic, unmodified handoff trajectory is detected."""

        if not execution_history:
            logger.debug("swarm_bus_loop_validation_skipped_empty_history")
            return True

        try:
            graph, edge_payloads, event_count = self._build_graph(execution_history)
        except ValueError:
            logger.exception("swarm_bus_loop_validation_invalid_history")
            raise

        has_cycle = self._has_cycle(graph)
        duplicate_unmodified_edge = self._history_has_duplicate_edge_payload(execution_history)
        repeated_static_edge = self._has_unmodified_repeat(edge_payloads)
        unsafe = has_cycle and (
            duplicate_unmodified_edge or repeated_static_edge or event_count > 1
        )

        if unsafe:
            logger.critical(
                "swarm_bus_infinite_loop_intercepted",
                extra={
                    "event_count": event_count,
                    "node_count": len(graph),
                    "max_loop_depth": self._max_loop_depth,
                },
            )
            return False

        logger.debug(
            "swarm_bus_loop_validation_passed",
            extra={"event_count": event_count, "node_count": len(graph)},
        )
        return True
