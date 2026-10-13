"""API key gate and VLA intake validation."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.catalog import load_catalog
from app.config import settings
from app.main import app, get_locker
from app.router import SemanticRouter


@pytest.fixture()
async def client() -> AsyncIterator[AsyncClient]:
    locker = AsyncMock()
    locker.acquire_task_lock = AsyncMock(return_value=True)
    app.dependency_overrides[get_locker] = lambda: locker
    app.state.model_catalog = load_catalog()
    app.state.semantic_router = SemanticRouter()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


CLAIM = {"task_id": "t1", "agent_id": "a1"}


@pytest.mark.asyncio
async def test_bus_open_when_no_key_configured(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "SYNAPSEMESH_API_KEY", "")
    r = await client.post("/v1/bus/claim", json=CLAIM)
    assert r.status_code == 200
    assert r.json()["acquired"] is True


@pytest.mark.parametrize(
    "path",
    ["/v1/bus/claim", "/v1/bus/renew", "/v1/bus/release", "/v1/bus/broadcast", "/v1/route/select"],
)
@pytest.mark.asyncio
async def test_protected_routes_reject_missing_key(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    monkeypatch.setattr(settings, "SYNAPSEMESH_API_KEY", "s3cret")
    r = await client.post(path, json=CLAIM)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


@pytest.mark.asyncio
async def test_wrong_key_rejected_and_right_key_accepted(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "SYNAPSEMESH_API_KEY", "s3cret")
    r = await client.post("/v1/bus/claim", json=CLAIM, headers={"X-API-Key": "nope"})
    assert r.status_code == 401
    r = await client.post("/v1/bus/claim", json=CLAIM, headers={"X-API-Key": "s3cret"})
    assert r.status_code == 200
    r = await client.post("/v1/bus/claim", json=CLAIM, headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_healthz_and_vla_stay_open_with_key(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "SYNAPSEMESH_API_KEY", "s3cret")
    assert (await client.get("/healthz")).status_code == 200
    r = await client.post("/v1/vla/commands", json={"frame_id": "f", "drift_score": 0.1})
    assert r.status_code == 200


@pytest.mark.parametrize("bad", ["abc", None, [1], {"x": 1}])
@pytest.mark.asyncio
async def test_vla_json_non_numeric_drift_is_422(client: AsyncClient, bad: object) -> None:
    r = await client.post("/v1/vla/commands", json={"frame_id": "f", "drift_score": bad})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_vla_non_finite_drift_is_422(client: AsyncClient) -> None:
    r = await client.post(
        "/v1/vla/commands",
        content=b'{"frame_id": "f", "drift_score": NaN}',
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 422
    r = await client.post("/v1/vla/commands", data={"frame_id": "f", "drift_score": "inf"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_vla_malformed_json_is_400(client: AsyncClient) -> None:
    r = await client.post(
        "/v1/vla/commands", content=b"{not json", headers={"content-type": "application/json"}
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_vla_upload_size_cap(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "VLA_MAX_UPLOAD_BYTES", 10)
    r = await client.post(
        "/v1/vla/commands",
        data={"frame_id": "f", "drift_score": "0.5"},
        files={"file": ("frame.bin", b"x" * 11)},
    )
    assert r.status_code == 413
    r = await client.post(
        "/v1/vla/commands",
        data={"frame_id": "f", "drift_score": "0.5"},
        files={"file": ("frame.bin", b"x" * 10)},
    )
    assert r.status_code == 200
    assert r.json()["high_fidelity_upload"] is True
    assert r.json()["motor_commands"] == [0.5, -0.25, 0.0]
