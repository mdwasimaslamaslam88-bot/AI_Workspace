from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AgentMission(Base):
    """Owner-scoped durable snapshot for the bounded Agent OS scheduler."""

    __tablename__ = "agent_missions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'needs_approval', 'planning', 'running', "
            "'paused', 'verifying', 'retrying', 'completed', 'failed', "
            "'cancelled', 'timed_out')",
            name="status_allowed",
        ),
        CheckConstraint(
            "char_length(request_json) BETWEEN 2 AND 65536",
            name="request_json_bounded",
        ),
        CheckConstraint(
            "char_length(record_json) BETWEEN 2 AND 1048576",
            name="record_json_bounded",
        ),
        CheckConstraint("revision BETWEEN 1 AND 16", name="revision_bounded"),
        CheckConstraint(
            "manual_retry_count BETWEEN 0 AND 3",
            name="manual_retry_count_bounded",
        ),
        CheckConstraint(
            "NOT approved OR requires_approval",
            name="approval_state_consistent",
        ),
        UniqueConstraint("id", "owner_id", name="uq_agent_missions_id_owner"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    request_json: Mapped[str] = mapped_column(Text, nullable=False)
    record_json: Mapped[str] = mapped_column(Text, nullable=False)
    pause_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    requires_approval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    approved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    revision: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default="1"
    )
    manual_retry_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AgentMissionEvent(Base):
    """Append-only, content-free mission lifecycle and owner-action audit."""

    __tablename__ = "agent_mission_events"
    __table_args__ = (
        CheckConstraint("sequence BETWEEN 1 AND 10000", name="sequence_bounded"),
        CheckConstraint(
            "char_length(action) BETWEEN 1 AND 32",
            name="action_bounded_non_blank",
        ),
        CheckConstraint(
            "status IN ('queued', 'needs_approval', 'planning', 'running', "
            "'paused', 'verifying', 'retrying', 'completed', 'failed', "
            "'cancelled', 'timed_out')",
            name="status_allowed",
        ),
        CheckConstraint(
            "detail_sha256 IS NULL OR detail_sha256 ~ '^[0-9a-f]{64}$'",
            name="detail_sha256_valid",
        ),
        UniqueConstraint(
            "mission_id", "sequence", name="uq_agent_mission_events_sequence"
        ),
        ForeignKeyConstraint(
            ("mission_id", "owner_id"),
            ("agent_missions.id", "agent_missions.owner_id"),
            name="fk_agent_mission_events_mission_owner",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    mission_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    step_id: Mapped[str | None] = mapped_column(String(64))
    attempt: Mapped[int | None] = mapped_column(SmallInteger)
    agent: Mapped[str | None] = mapped_column(String(32))
    model_id: Mapped[str | None] = mapped_column(String(96))
    detail_sha256: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


Index("ix_agent_missions_owner_created", AgentMission.owner_id, AgentMission.created_at)
Index("ix_agent_missions_status", AgentMission.status)
Index(
    "ix_agent_mission_events_owner_mission_sequence",
    AgentMissionEvent.owner_id,
    AgentMissionEvent.mission_id,
    AgentMissionEvent.sequence,
)
