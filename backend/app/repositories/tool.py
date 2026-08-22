from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select, update

from app.models.conversation import Conversation
from app.models.message import Message
from app.models.tool import ToolExecution, ToolExecutionStatus
from app.repositories.base import BaseRepository

MAX_TOOL_EXECUTION_HISTORY = 100


class ToolRepository(BaseRepository):
    async def create_running(
        self,
        owner_id: UUID,
        conversation_id: UUID | None,
        tool_name: str,
        permission: str,
        arguments_json: str,
        *,
        initiator: str,
    ) -> ToolExecution:
        execution = ToolExecution(
            owner_id=owner_id,
            conversation_id=conversation_id,
            tool_name=tool_name,
            permission=permission,
            status=ToolExecutionStatus.RUNNING,
            initiator=initiator,
            arguments_json=arguments_json,
        )
        self.session.add(execution)
        await self.session.flush()
        return execution

    async def reconcile_interrupted(self) -> int:
        result = await self.session.execute(
            update(ToolExecution)
            .where(ToolExecution.status == ToolExecutionStatus.RUNNING)
            .values(
                status=ToolExecutionStatus.FAILED,
                result_json=None,
                error_code="server_restarted",
                duration_ms=0,
                completed_at=func.now(),
            )
        )
        return result.rowcount

    async def finish(
        self,
        owner_id: UUID,
        execution_id: UUID,
        status: ToolExecutionStatus,
        duration_ms: int,
        *,
        result_json: str | None = None,
        error_code: str | None = None,
    ) -> ToolExecution | None:
        result = await self.session.execute(
            update(ToolExecution)
            .where(
                ToolExecution.id == execution_id,
                ToolExecution.owner_id == owner_id,
                ToolExecution.status == ToolExecutionStatus.RUNNING,
            )
            .values(
                status=status,
                result_json=result_json,
                error_code=error_code,
                duration_ms=duration_ms,
                completed_at=func.now(),
            )
            .returning(ToolExecution)
        )
        return result.scalar_one_or_none()

    async def list_for_owner(
        self, owner_id: UUID, *, limit: int = 50
    ) -> tuple[ToolExecution, ...]:
        if not 1 <= limit <= MAX_TOOL_EXECUTION_HISTORY:
            raise ValueError("tool execution history limit is outside its bound")
        result = await self.session.execute(
            select(ToolExecution)
            .where(ToolExecution.owner_id == owner_id)
            .order_by(ToolExecution.started_at.desc(), ToolExecution.id.desc())
            .limit(limit)
        )
        return tuple(result.scalars().all())

    async def conversation_exists_for_owner(
        self, owner_id: UUID, conversation_id: UUID
    ) -> bool:
        result = await self.session.execute(
            select(Conversation.id).where(
                Conversation.id == conversation_id,
                Conversation.owner_id == owner_id,
            )
        )
        return result.scalar_one_or_none() is not None

    async def search_conversations_for_owner(
        self,
        owner_id: UUID,
        query: str,
        *,
        limit: int,
        conversation_id: UUID | None,
    ) -> tuple[tuple[UUID, UUID, str | None, int, str, str], ...]:
        escaped = (
            query.lower()
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        statement = (
            select(
                Message.id,
                Conversation.id,
                Conversation.title,
                Message.sequence_number,
                Message.role,
                Message.content,
            )
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Conversation.owner_id == owner_id,
                func.lower(Message.content).like(f"%{escaped}%", escape="\\"),
            )
        )
        if conversation_id is not None:
            statement = statement.where(Conversation.id == conversation_id)
        result = await self.session.execute(
            statement.order_by(Message.created_at.desc(), Message.id.desc()).limit(limit)
        )
        return tuple(
            (row[0], row[1], row[2], row[3], row[4].value, row[5])
            for row in result.all()
        )
