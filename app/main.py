"""FastAPI management plane and analytics ingress for SynapseMesh."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Base, engine, get_db
from app.mesh import AutonomousConsensusMesh
from app.models import ModelPerformanceAudit

DbSession = Annotated[AsyncSession, Depends(get_db)]


class NegotiateRequest(BaseModel):
    """Request body for the mesh negotiation endpoint."""

    task: str = Field(min_length=1)


class NegotiateResponse(BaseModel):
    """Response body for a negotiated workflow."""

    audit_id: str
    execution_state: str
    classification: str
    actor_model: str
    critic_model: str
    convergence_turns: int
    total_tokens: int
    elapsed_latency_ms: float
    telemetry_signature: str
    route_policy: dict[str, Any]
    final_product: str
    history: list[dict[str, Any]]


@asynccontextmanager
async def lifespan(_: FastAPI) -> Any:
    """Initialize database metadata at startup."""

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(
    title="SynapseMesh",
    version="0.1.0",
    description="Autonomous multi-model negotiation loop and semantic routing gateway.",
    lifespan=lifespan,
)


@app.post("/v1/mesh/negotiate", response_model=NegotiateResponse)
async def negotiate_mesh(request: NegotiateRequest, db: DbSession) -> NegotiateResponse:
    """Run an actor-critic negotiation workflow."""

    mesh = AutonomousConsensusMesh()
    result = await mesh.negotiate_workflow(db, request.task)
    return NegotiateResponse.model_validate(result)


@app.get("/v1/mesh/telemetry")
async def mesh_telemetry(db: DbSession) -> dict[str, Any]:
    """Return aggregate performance analytics across model pairs."""

    total_result = await db.execute(select(func.count(ModelPerformanceAudit.id)))
    total_runs = int(total_result.scalar_one())

    grouped_result = await db.execute(
        select(
            ModelPerformanceAudit.actor_model,
            ModelPerformanceAudit.critic_model,
            func.count(ModelPerformanceAudit.id),
            func.avg(ModelPerformanceAudit.convergence_turns),
            func.avg(ModelPerformanceAudit.elapsed_latency_ms),
            func.sum(ModelPerformanceAudit.total_tokens),
            func.sum(
                case(
                    (ModelPerformanceAudit.execution_state == "SUCCESS", 1),
                    else_=0,
                )
            ),
        ).group_by(ModelPerformanceAudit.actor_model, ModelPerformanceAudit.critic_model)
    )

    model_pairs: list[dict[str, Any]] = []
    for row in grouped_result.all():
        run_count = int(row[2])
        success_count = int(row[6] or 0)
        model_pairs.append(
            {
                "actor_model": row[0],
                "critic_model": row[1],
                "runs": run_count,
                "average_turns_to_consensus": float(row[3] or 0.0),
                "average_latency_ms": float(row[4] or 0.0),
                "total_tokens": int(row[5] or 0),
                "success_ratio": success_count / run_count if run_count else 0.0,
            }
        )

    state_result = await db.execute(
        select(
            ModelPerformanceAudit.execution_state,
            func.count(ModelPerformanceAudit.id),
        ).group_by(ModelPerformanceAudit.execution_state)
    )
    states = {str(row[0]): int(row[1]) for row in state_result.all()}
    return {
        "total_runs": total_runs,
        "states": states,
        "model_pairs": model_pairs,
    }
