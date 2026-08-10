from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.services.message as message_service_module
from app.models import MessageRole
from app.services.message import MessageAppendConflictError, MessageService


def _service_session(sequence_number: int | None = 1):
    session = AsyncMock(spec=AsyncSession)
    result = Mock()
    result.scalar_one_or_none.return_value = sequence_number
    session.execute.return_value = result
    return session


@pytest.mark.asyncio
async def test_service_commits_once_after_all_repository_work_succeeds():
    events: list[str] = []
    session = _service_session(3)
    result = session.execute.return_value

    async def execute(_statement):
        events.append("allocate")
        return result

    async def flush():
        events.append("flush")

    async def commit():
        events.append("commit")

    session.execute.side_effect = execute
    session.add.side_effect = lambda _message: events.append("add")
    session.flush.side_effect = flush
    session.commit.side_effect = commit
    service = MessageService(session)

    message = await service.append_for_owner(
        uuid4(),
        uuid4(),
        MessageRole.USER,
        "question",
    )

    assert message is not None
    assert message.sequence_number == 3
    assert events == ["allocate", "add", "flush", "commit"]
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_service_rolls_back_owner_scoped_miss_without_committing():
    session = _service_session(None)
    service = MessageService(session)

    message = await service.append_for_owner(
        uuid4(),
        uuid4(),
        MessageRole.USER,
        "question",
    )

    assert message is None
    session.rollback.assert_awaited_once_with()
    session.commit.assert_not_awaited()
    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_service_rolls_back_partial_append_when_flush_fails():
    session = _service_session(5)
    session.flush.side_effect = RuntimeError("insert failed")
    service = MessageService(session)

    with pytest.raises(RuntimeError, match="insert failed"):
        await service.append_for_owner(
            uuid4(),
            uuid4(),
            MessageRole.ASSISTANT,
            "answer",
        )

    session.execute.assert_awaited_once()
    session.add.assert_called_once()
    session.flush.assert_awaited_once_with()
    session.rollback.assert_awaited_once_with()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_service_rolls_back_when_commit_fails():
    session = _service_session(8)
    session.commit.side_effect = RuntimeError("commit failed")
    service = MessageService(session)

    with pytest.raises(RuntimeError, match="commit failed"):
        await service.append_for_owner(
            uuid4(),
            uuid4(),
            MessageRole.TOOL,
            "tool result",
        )

    session.commit.assert_awaited_once_with()
    session.rollback.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_integrity_error_is_rolled_back_before_translation(monkeypatch):
    events: list[str] = []
    session = _service_session(13)
    integrity_error = IntegrityError("insert message", {}, RuntimeError("unique"))

    async def failing_flush():
        events.append("flush")
        raise integrity_error

    async def rollback():
        events.append("rollback")

    class ObservedConflictError(MessageAppendConflictError):
        def __init__(self, message):
            events.append("translate")
            super().__init__(message)

    session.flush.side_effect = failing_flush
    session.rollback.side_effect = rollback
    monkeypatch.setattr(
        message_service_module,
        "MessageAppendConflictError",
        ObservedConflictError,
    )
    service = MessageService(session)

    with pytest.raises(ObservedConflictError) as caught:
        await service.append_for_owner(
            uuid4(),
            uuid4(),
            MessageRole.USER,
            "content must not appear in the translated error",
        )

    assert events == ["flush", "rollback", "translate"]
    assert str(caught.value) == "message append violated a persistence constraint"
    assert caught.value.__cause__ is integrity_error
    session.rollback.assert_awaited_once_with()
    session.commit.assert_not_awaited()
