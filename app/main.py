"""FastAPI ingress for the SwarmBus network coordination fabric."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Annotated, Any, cast

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.broker import BrokerError, StreamBroker
from app.config import settings
from app.detector import DeadlockLoopInterceptor
from app.locker import DistributedAgentLocker, LockError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)
VLA_FILE = File(default=None)
VLA_FRAME_ID = Form(default=None, alias="frame_id")
VLA_DRIFT_SCORE = Form(default=None, alias="drift_score")
VLA_FRAME_STATE = Form(default=None, alias="frame_state")


class BroadcastRequest(BaseModel):
    """Agent event submission payload for stream publication."""

    topic: str = Field(min_length=1)
    event: dict[str, Any] = Field(default_factory=dict)
    execution_history: list[dict[str, Any]] = Field(default_factory=list)


class BroadcastResponse(BaseModel):
    """Committed event identity returned by the broker."""

    topic: str
    event_id: str
    accepted: bool


class ClaimRequest(BaseModel):
    """Task lock claim request submitted by an agent."""

    task_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)


class ClaimResponse(BaseModel):
    """Task lock claim result."""

    task_id: str
    agent_id: str
    acquired: bool


class VLACommandResponse(BaseModel):
    """Accepted VLA command routing result."""

    accepted: bool
    frame_id: str
    frame_state: str
    dispatch: str
    high_fidelity_upload: bool
    drift_score: float
    motor_commands: list[float]


@asynccontextmanager
async def lifespan(application: FastAPI) -> Any:
    redis_client = Redis.from_url(settings.REDIS_URL, decode_responses=False)
    application.state.redis = redis_client
    application.state.broker = StreamBroker(redis_client)
    application.state.locker = DistributedAgentLocker(redis_client)
    application.state.detector = DeadlockLoopInterceptor()
    logger.info(
        "swarm_bus_application_started",
        extra={"environment": settings.AGENT_BUS_ENV, "redis_url": settings.REDIS_URL},
    )
    try:
        yield
    finally:
        await redis_client.aclose()
        logger.info("swarm_bus_application_stopped")


app = FastAPI(
    title="SynapseMesh",
    version="0.1.0",
    description="Model-routing mesh and VLA command intake service.",
    lifespan=lifespan,
)


def get_broker(request: Request) -> StreamBroker:
    return cast(StreamBroker, request.app.state.broker)


def get_locker(request: Request) -> DistributedAgentLocker:
    return cast(DistributedAgentLocker, request.app.state.locker)


def get_detector(request: Request) -> DeadlockLoopInterceptor:
    return cast(DeadlockLoopInterceptor, request.app.state.detector)


BrokerDependency = Annotated[StreamBroker, Depends(get_broker)]
DetectorDependency = Annotated[DeadlockLoopInterceptor, Depends(get_detector)]
LockerDependency = Annotated[DistributedAgentLocker, Depends(get_locker)]


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "environment": settings.AGENT_BUS_ENV}


@app.post("/v1/vla/commands", response_model=VLACommandResponse)
async def accept_vla_command(
    request: Request,
    file: UploadFile | None = VLA_FILE,
    form_frame_id: str | None = VLA_FRAME_ID,
    form_drift_score: float | None = VLA_DRIFT_SCORE,
    form_frame_state: str | None = VLA_FRAME_STATE,
) -> VLACommandResponse:
    """Accept SpatialFlux VLA telemetry and anomaly frame uploads."""

    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="JSON body must be an object")
        frame_id = str(payload.get("frame_id", "unknown-frame"))
        drift_score = float(payload.get("drift_score", 0.0))
        frame_state = str(payload.get("frame_state", "NOMINAL_STABLE"))
        high_fidelity_upload = bool(payload.get("high_fidelity_upload", False))
    else:
        frame_id = form_frame_id or "unknown-frame"
        drift_score = float(form_drift_score or 0.0)
        frame_state = form_frame_state or "ANOMALY_TRIGGERED"
        high_fidelity_upload = file is not None
        if file is not None:
            await file.read()

    return VLACommandResponse(
        accepted=True,
        frame_id=frame_id,
        frame_state=frame_state,
        dispatch="local_vla_command_sink",
        high_fidelity_upload=high_fidelity_upload,
        drift_score=drift_score,
        motor_commands=_deterministic_motor_commands(drift_score, frame_state),
    )


def _deterministic_motor_commands(drift_score: float, frame_state: str) -> list[float]:
    if frame_state == "NOMINAL_STABLE":
        return [0.0, 0.0, 0.0]
    bounded = max(-1.0, min(1.0, drift_score))
    return [round(bounded, 4), round(-bounded / 2, 4), 0.0]


@app.post("/v1/bus/broadcast", response_model=BroadcastResponse)
async def broadcast_event(
    payload: BroadcastRequest,
    broker: BrokerDependency,
    detector: DetectorDependency,
) -> BroadcastResponse:
    try:
        if not detector.validate_trajectory(payload.execution_history):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="execution trajectory rejected by loop interceptor",
            )
        event_id = await broker.publish_event(payload.topic, payload.event)
    except HTTPException:
        raise
    except (BrokerError, ValueError) as exc:
        logger.exception(
            "swarm_bus_broadcast_failed",
            extra={"topic": payload.topic, "error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="event could not be published",
        ) from exc

    return BroadcastResponse(topic=payload.topic, event_id=event_id, accepted=True)


@app.post("/v1/bus/claim", response_model=ClaimResponse)
async def claim_task(
    payload: ClaimRequest,
    locker: LockerDependency,
) -> ClaimResponse:
    try:
        acquired = await locker.acquire_task_lock(payload.task_id, payload.agent_id)
    except (LockError, ValueError) as exc:
        logger.exception(
            "swarm_bus_claim_failed",
            extra={"task_id": payload.task_id, "agent_id": payload.agent_id},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="task lock could not be claimed",
        ) from exc

    return ClaimResponse(task_id=payload.task_id, agent_id=payload.agent_id, acquired=acquired)
