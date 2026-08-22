from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

MAX_TOOL_ARGUMENT_JSON_CHARACTERS = 8_192
MAX_TOOL_RESULT_JSON_CHARACTERS = 16_384


class ToolExecutionStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class ToolExecution(Base):
    __tablename__ = "tool_executions"
    __table_args__ = (
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
            "status IN ('running', 'completed', 'failed', 'timed_out', 'cancelled')",
            name="status_allowed",
        ),
        CheckConstraint(
            "initiator IN ('explicit_user', 'workflow')",
            name="initiator_allowed",
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
            "'server_restarted')",
            name="error_code_allowed",
        ),
        CheckConstraint(
            "(status = 'running' AND completed_at IS NULL AND result_json IS NULL "
            "AND error_code IS NULL AND duration_ms IS NULL) OR "
            "(status = 'completed' AND completed_at IS NOT NULL "
            "AND result_json IS NOT NULL AND error_code IS NULL AND duration_ms >= 0) OR "
            "(status IN ('failed', 'timed_out', 'cancelled') "
            "AND completed_at IS NOT NULL AND result_json IS NULL "
            "AND error_code IS NOT NULL AND duration_ms >= 0)",
            name="terminal_state_consistent",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    conversation_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL")
    )
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    permission: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[ToolExecutionStatus] = mapped_column(
        Enum(
            ToolExecutionStatus,
            name="tool_execution_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=lambda enum_type: [member.value for member in enum_type],
            length=32,
        ),
        nullable=False,
        default=ToolExecutionStatus.RUNNING,
    )
    initiator: Mapped[str] = mapped_column(String(32), nullable=False, default="explicit_user")
    arguments_json: Mapped[str] = mapped_column(Text, nullable=False)
    result_json: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)


Index(
    "ix_tool_executions_owner_started_at",
    ToolExecution.owner_id,
    ToolExecution.started_at.desc(),
)
Index(
    "ix_tool_executions_owner_conversation",
    ToolExecution.owner_id,
    ToolExecution.conversation_id,
)
