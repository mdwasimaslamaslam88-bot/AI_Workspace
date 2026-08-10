from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import Update

from app.models import Message, MessageRole
from app.repositories.message import MessageRepository


def _session_with_allocated_sequence(sequence_number: int | None):
    session = AsyncMock(spec=AsyncSession)
    result = Mock()
    result.scalar_one_or_none.return_value = sequence_number
    session.execute.return_value = result
    return session


@pytest.mark.asyncio
async def test_append_allocates_sequence_with_owner_scoped_atomic_update():
    owner_id = uuid4()
    conversation_id = uuid4()
    session = _session_with_allocated_sequence(7)
    repository = MessageRepository(session)

    message = await repository.append_for_owner(
        owner_id,
        conversation_id,
        MessageRole.USER,
        "  unchanged content  ",
    )

    statement = session.execute.await_args.args[0]
    assert isinstance(statement, Update)

    compiled = statement.compile(dialect=postgresql.dialect())
    sql = " ".join(str(compiled).split()).lower()
    assert sql.startswith("update conversations set")
    assert "next_message_sequence=(conversations.next_message_sequence +" in sql
    assert "where conversations.id =" in sql
    assert "and conversations.owner_id =" in sql
    assert "returning conversations.next_message_sequence -" in sql
    assert "as allocated_sequence_number" in sql
    assert "max(" not in sql
    assert owner_id in compiled.params.values()
    assert conversation_id in compiled.params.values()

    assert message is not None
    assert message.conversation_id == conversation_id
    assert message.role is MessageRole.USER
    assert message.content == "  unchanged content  "
    assert message.sequence_number == 7
    assert "owner_id" not in Message.__table__.c
    assert "owner_id" not in message.__dict__

    session.add.assert_called_once_with(message)
    session.flush.assert_awaited_once_with()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_sequence_update_precedes_insert_and_flush_on_the_same_session():
    events: list[str] = []
    result = Mock()
    result.scalar_one_or_none.return_value = 11
    session = AsyncMock(spec=AsyncSession)

    async def execute(_statement):
        events.append("allocate")
        return result

    async def flush():
        events.append("flush")

    session.execute.side_effect = execute
    session.add.side_effect = lambda _message: events.append("add")
    session.flush.side_effect = flush
    repository = MessageRepository(session)

    message = await repository.append_for_owner(
        uuid4(),
        uuid4(),
        MessageRole.ASSISTANT,
        "answer",
    )

    assert message is not None
    assert message.sequence_number == 11
    assert events == ["allocate", "add", "flush"]
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("ownership_case", ["missing", "different-owner"])
async def test_owner_scoped_miss_has_one_consistent_result(ownership_case):
    session = _session_with_allocated_sequence(None)
    repository = MessageRepository(session)

    message = await repository.append_for_owner(
        uuid4(),
        uuid4(),
        MessageRole.SYSTEM,
        ownership_case,
    )

    assert message is None
    session.execute.assert_awaited_once()
    session.add.assert_not_called()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()
