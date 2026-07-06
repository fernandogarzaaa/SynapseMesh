"""State and telemetry schemas for SynapseMesh."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON, TypeDecorator

from app.database import Base


class ExecutionState(StrEnum):
    """Terminal state for a model negotiation audit."""

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    BREACHED = "BREACHED"


class PortableUUID(TypeDecorator[uuid.UUID]):
    """Use PostgreSQL UUID in production and string UUID in test dialects."""

    impl = String(36)
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(UUID(as_uuid=True))
        return dialect.type_descriptor(String(36))

    def process_bind_param(
        self,
        value: uuid.UUID | str | None,
        dialect: Any,
    ) -> uuid.UUID | str | None:
        if value is None:
            return None
        parsed = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        return parsed if dialect.name == "postgresql" else str(parsed)

    def process_result_value(self, value: uuid.UUID | str | None, dialect: Any) -> uuid.UUID | None:
        if value is None:
            return None
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


class PortableJSONB(TypeDecorator[dict[str, Any]]):
    """Use PostgreSQL JSONB in production and generic JSON elsewhere."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


def utc_now() -> datetime:
    """Return an aware UTC timestamp."""

    return datetime.now(UTC)


class ModelPerformanceAudit(Base):
    """Telemetry record for one actor-critic negotiation run."""

    __tablename__ = "model_performance_audits"

    id: Mapped[uuid.UUID] = mapped_column(PortableUUID(), primary_key=True, default=uuid.uuid4)
    task_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    actor_model: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    critic_model: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    elapsed_latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    convergence_turns: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    execution_state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    raw_negotiation_history: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(PortableJSONB()),
        nullable=False,
        default=dict,
    )
    telemetry_signature: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
