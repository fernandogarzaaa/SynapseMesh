"""Integration tests for the SynapseMesh consensus engine."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from app.database import Base
from app.mesh import CONSENSUS_ACCEPTED, AutonomousConsensusMesh
from app.models import ExecutionState, ModelPerformanceAudit


class MockResponse:
    """Minimal HTTPX-like response used by mocked provider calls."""

    def __init__(self, content: str) -> None:
        self._content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, str]:
        return {"content": self._content}


@pytest.fixture()
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


def build_test_settings() -> Settings:
    return Settings(
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
        OPENAI_API_URL="https://public.openai.invalid/v1",
        ANTHROPIC_API_URL="https://public.anthropic.invalid/v1",
        INTERNAL_OPENAI_API_URL="http://internal.openai.local/v1",
        INTERNAL_ANTHROPIC_API_URL="http://internal.anthropic.local/v1",
        MAX_CONCURRENT_STREAM_LOOPS=5,
        COMPLIANCE_MODE="STRICT",
    )


@pytest.mark.asyncio()
async def test_successful_negotiation_stops_and_commits_success(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    http_client = AsyncMock()
    http_client.post = AsyncMock(
        side_effect=[
            MockResponse("implementation asset"),
            MockResponse(CONSENSUS_ACCEPTED),
        ]
    )
    mesh = AutonomousConsensusMesh(runtime_settings=build_test_settings(), http_client=http_client)

    async with session_factory() as session:
        result = await mesh.negotiate_workflow(session, "standard formatting implementation file")

    assert result["execution_state"] == ExecutionState.SUCCESS.value
    assert result["convergence_turns"] == 1
    assert http_client.post.await_count == 2

    async with session_factory() as session:
        audit_result = await session.execute(select(ModelPerformanceAudit))
        audit = audit_result.scalar_one()

    assert audit.execution_state == ExecutionState.SUCCESS.value
    assert audit.raw_negotiation_history["history"][1]["content"] == CONSENSUS_ACCEPTED
    assert audit.total_tokens > 0
    assert len(audit.telemetry_signature) == 64


@pytest.mark.asyncio()
async def test_restricted_payload_isolates_routes_from_public_endpoints(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    call_payloads: list[dict[str, Any]] = []

    async def capture_post(endpoint: str, json: dict[str, Any]) -> MockResponse:
        call_payloads.append({"endpoint": endpoint, "json": json})
        if json["role"] == "critic":
            return MockResponse(CONSENSUS_ACCEPTED)
        return MockResponse("internal-only implementation")

    http_client = AsyncMock()
    http_client.post = AsyncMock(side_effect=capture_post)
    runtime_settings = build_test_settings()
    mesh = AutonomousConsensusMesh(runtime_settings=runtime_settings, http_client=http_client)
    restricted_task = (
        "Analyze confidential ARR and cash burn metrics without unauthorized exposure."
    )

    async with session_factory() as session:
        result = await mesh.negotiate_workflow(session, restricted_task)

    assert result["classification"] == "RESTRICTED_BOUNDS"
    assert result["route_policy"]["allow_public_endpoints"] is False
    assert http_client.post.await_count == 2
    assert all("internal" in call["endpoint"] for call in call_payloads)
    assert all(call["json"]["public_endpoint_allowed"] is False for call in call_payloads)

    async with session_factory() as session:
        audit_result = await session.execute(select(ModelPerformanceAudit))
        audit = audit_result.scalar_one()

    assert audit.execution_state == ExecutionState.SUCCESS.value
    assert audit.raw_negotiation_history["classification"] == "RESTRICTED_BOUNDS"
    assert audit.raw_negotiation_history["route_policy"]["allow_public_endpoints"] is False
