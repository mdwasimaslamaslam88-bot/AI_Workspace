from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.tool import ToolExecution

MAX_WORKFLOW_STEPS = 8
MAX_WORKFLOW_NAME_CHARACTERS = 120
MAX_WORKFLOW_RESULT_JSON_CHARACTERS = 65_536


class WorkflowStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class Workflow(Base):
    __tablename__ = "workflows"
    __table_args__ = (
        CheckConstraint(
            "name IS NULL OR (char_length(name) BETWEEN 1 AND 120 "
            "AND char_length(btrim(name)) > 0)",
            name="name_bounded_non_blank",
        ),
        CheckConstraint("step_count BETWEEN 1 AND 8", name="step_count_bounded"),
        CheckConstraint(
            "current_step_position IS NULL OR current_step_position "
            "BETWEEN 1 AND step_count",
            name="current_step_position_bounded",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', "
            "'cancelled', 'timed_out')",
            name="status_allowed",
        ),
        CheckConstraint(
            "result_json IS NULL OR char_length(result_json) <= 65536",
            name="result_json_bounded",
        ),
        CheckConstraint(
            "error_code IS NULL OR error_code IN ('workflow_cancelled', "
            "'workflow_timed_out', 'step_failed', 'output_too_large', "
            "'server_restarted', 'internal_failure')",
            name="error_code_allowed",
        ),
        CheckConstraint(
            "(status = 'pending' AND started_at IS NULL AND completed_at IS NULL "
            "AND current_step_position IS NULL AND result_json IS NULL "
            "AND error_code IS NULL AND cancel_requested = false) OR "
            "(status = 'running' AND started_at IS NOT NULL "
            "AND completed_at IS NULL AND current_step_position IS NOT NULL "
            "AND result_json IS NULL AND error_code IS NULL) OR "
            "(status = 'completed' AND started_at IS NOT NULL "
            "AND completed_at IS NOT NULL AND current_step_position IS NOT NULL "
            "AND result_json IS NOT NULL AND error_code IS NULL "
            "AND cancel_requested = false) OR "
            "(status IN ('failed', 'cancelled', 'timed_out') "
            "AND completed_at IS NOT NULL AND result_json IS NULL "
            "AND error_code IS NOT NULL)",
            name="lifecycle_consistent",
        ),
        UniqueConstraint("id", "owner_id", name="uq_workflows_id_owner"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str | None] = mapped_column(String(MAX_WORKFLOW_NAME_CHARACTERS))
    status: Mapped[WorkflowStatus] = mapped_column(
        Enum(
            WorkflowStatus,
            name="workflow_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=lambda enum_type: [member.value for member in enum_type],
            length=32,
        ),
        nullable=False,
        default=WorkflowStatus.PENDING,
    )
    step_count: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    current_step_position: Mapped[int | None] = mapped_column(SmallInteger)
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    result_json: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    steps: Mapped[list[WorkflowStep]] = relationship(
        back_populates="workflow",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="WorkflowStep.position",
        passive_deletes=True,
    )


class WorkflowStep(Base):
    __tablename__ = "workflow_steps"
    __table_args__ = (
        CheckConstraint("position BETWEEN 1 AND 8", name="position_bounded"),
        CheckConstraint(
            "tool_name IN ('calculator', 'local_time', 'document_search', "
            "'conversation_search', 'memory_search')",
            name="tool_name_allowed",
        ),
        CheckConstraint(
            "permission IN ('utility', 'personal_documents_read', "
            "'personal_conversations_read', 'personal_memory_read')",
            name="permission_allowed",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', "
            "'cancelled', 'timed_out')",
            name="status_allowed",
        ),
        CheckConstraint(
            "char_length(arguments_json) BETWEEN 2 AND 8192",
            name="arguments_json_bounded",
        ),
        CheckConstraint(
            "result_json IS NULL OR char_length(result_json) <= 16384",
            name="result_json_bounded",
        ),
        CheckConstraint(
            "error_code IS NULL OR error_code IN ('tool_timed_out', "
            "'tool_cancelled', 'tool_execution_failed', 'tool_unavailable', "
            "'step_timed_out', 'workflow_cancelled', 'server_restarted', "
            "'not_run', 'internal_failure')",
            name="error_code_allowed",
        ),
        CheckConstraint(
            "(status = 'pending' AND started_at IS NULL AND completed_at IS NULL "
            "AND tool_execution_id IS NULL AND result_json IS NULL "
            "AND error_code IS NULL AND duration_ms IS NULL) OR "
            "(status = 'running' AND started_at IS NOT NULL "
            "AND completed_at IS NULL AND result_json IS NULL "
            "AND error_code IS NULL AND duration_ms IS NULL) OR "
            "(status = 'completed' AND started_at IS NOT NULL "
            "AND completed_at IS NOT NULL AND tool_execution_id IS NOT NULL "
            "AND result_json IS NOT NULL AND error_code IS NULL "
            "AND duration_ms >= 0) OR "
            "(status IN ('failed', 'cancelled', 'timed_out') "
            "AND completed_at IS NOT NULL AND result_json IS NULL "
            "AND error_code IS NOT NULL AND duration_ms >= 0)",
            name="lifecycle_consistent",
        ),
        UniqueConstraint(
            "workflow_id", "position", name="uq_workflow_steps_position"
        ),
        ForeignKeyConstraint(
            ("workflow_id", "owner_id"),
            ("workflows.id", "workflows.owner_id"),
            name="fk_workflow_steps_workflow_owner_workflows",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workflow_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
    )
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    permission: Mapped[str] = mapped_column(String(64), nullable=False)
    arguments_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[WorkflowStatus] = mapped_column(
        Enum(
            WorkflowStatus,
            name="workflow_step_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=lambda enum_type: [member.value for member in enum_type],
            length=32,
        ),
        nullable=False,
        default=WorkflowStatus.PENDING,
    )
    tool_execution_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tool_executions.id", ondelete="SET NULL"),
    )
    result_json: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)

    workflow: Mapped[Workflow] = relationship(back_populates="steps", lazy="raise")
    tool_execution: Mapped[ToolExecution | None] = relationship(lazy="raise")


Index(
    "ix_workflows_owner_created_at",
    Workflow.owner_id,
    Workflow.created_at.desc(),
)
Index(
    "ix_workflows_owner_status",
    Workflow.owner_id,
    Workflow.status,
)
Index(
    "ix_workflow_steps_owner_workflow_position",
    WorkflowStep.owner_id,
    WorkflowStep.workflow_id,
    WorkflowStep.position,
)
