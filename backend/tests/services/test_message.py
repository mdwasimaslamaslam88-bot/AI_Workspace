from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.services.message as message_service_module
from app.models import Message, MessageRole
from app.models.message import (
    MAX_MESSAGE_CONTENT_CHARACTERS,
    MessageContentTooLargeError,
)
from app.repositories.message import MessageCursor, MessagePagination
from app.services.message import MessageAppendConflictError, MessageService


def _service_session(sequence_number: int | None = 1, *, rows=()):
    session = AsyncMock(spec=AsyncSession)
    result = Mock()
    result.scalar_one_or_none.return_value = sequence_number
    result.scalars.return_value.all.return_value = list(rows)
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
async def test_service_rejects_oversized_content_before_persistence_work():
    session = _service_session(3)
    service = MessageService(session)

    with pytest.raises(MessageContentTooLargeError) as captured:
        await service.append_for_owner(
            uuid4(),
            uuid4(),
            MessageRole.USER,
            "x" * (MAX_MESSAGE_CONTENT_CHARACTERS + 1),
        )

    assert str(captured.value) == "persisted text is too large"
    session.execute.assert_not_awaited()
    session.add.assert_not_called()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()
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
    error = RuntimeError("insert failed")
    session.flush.side_effect = error
    service = MessageService(session)

    with pytest.raises(RuntimeError) as caught:
        await service.append_for_owner(
            uuid4(),
            uuid4(),
            MessageRole.ASSISTANT,
            "answer",
        )

    assert caught.value is error
    session.execute.assert_awaited_once()
    session.add.assert_called_once()
    session.flush.assert_awaited_once_with()
    session.rollback.assert_awaited_once_with()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_service_rolls_back_when_commit_fails():
    session = _service_session(8)
    error = RuntimeError("commit failed")
    session.commit.side_effect = error
    service = MessageService(session)

    with pytest.raises(RuntimeError) as caught:
        await service.append_for_owner(
            uuid4(),
            uuid4(),
            MessageRole.TOOL,
            "tool result",
        )

    assert caught.value is error
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


@pytest.mark.asyncio
async def test_message_list_returns_page_without_committing():
    conversation_id = uuid4()
    first = Message(
        conversation_id=conversation_id,
        role=MessageRole.USER,
        content="first",
        sequence_number=1,
    )
    second = Message(
        conversation_id=conversation_id,
        role=MessageRole.ASSISTANT,
        content="second",
        sequence_number=2,
    )
    session = _service_session(None, rows=(first, second))
    service = MessageService(session)

    page = await service.list_for_owner(
        uuid4(),
        conversation_id,
        MessagePagination(limit=1),
    )

    assert page.items == (first,)
    assert page.next_cursor == MessageCursor(sequence_number=1)
    session.execute.assert_awaited_once()
    session.add.assert_not_called()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_generation_context_forwards_internal_bound_without_committing(
    monkeypatch,
):
    conversation_id = uuid4()
    owner_id = uuid4()
    context = (
        Message(
            conversation_id=conversation_id,
            role=MessageRole.USER,
            content="question",
            sequence_number=1,
        ),
    )
    session = _service_session(None)
    service = MessageService(session)
    context_read = AsyncMock(return_value=context)
    monkeypatch.setattr(
        service.repository,
        "list_generation_context_for_owner",
        context_read,
    )

    result = await service.list_generation_context_for_owner(
        owner_id,
        conversation_id,
        max_messages=100,
    )

    assert result == context
    context_read.assert_awaited_once_with(
        owner_id,
        conversation_id,
        max_messages=100,
    )
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_generation_context_failure_rolls_back_original_exception(
    monkeypatch,
):
    session = _service_session(None)
    service = MessageService(session)
    error = RuntimeError("context read failed")
    context_read = AsyncMock(side_effect=error)
    monkeypatch.setattr(
        service.repository,
        "list_generation_context_for_owner",
        context_read,
    )

    with pytest.raises(RuntimeError) as caught:
        await service.list_generation_context_for_owner(
            uuid4(),
            uuid4(),
            max_messages=100,
        )

    assert caught.value is error
    session.rollback.assert_awaited_once_with()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_message_list_failure_rolls_back_and_preserves_original_exception():
    events: list[str] = []
    session = _service_session(None)
    error = RuntimeError("list failed")

    async def execute(_statement):
        events.append("execute")
        raise error

    async def rollback():
        events.append("rollback")

    session.execute.side_effect = execute
    session.rollback.side_effect = rollback
    service = MessageService(session)

    with pytest.raises(RuntimeError) as caught:
        await service.list_for_owner(uuid4(), uuid4())

    assert caught.value is error
    assert events == ["execute", "rollback"]
    session.rollback.assert_awaited_once_with()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_append_allocation_failure_rolls_back_original_exception():
    events: list[str] = []
    session = _service_session(None)
    error = RuntimeError("allocation failed")

    async def execute(_statement):
        events.append("execute")
        raise error

    async def rollback():
        events.append("rollback")

    session.execute.side_effect = execute
    session.rollback.side_effect = rollback
    service = MessageService(session)

    with pytest.raises(RuntimeError) as caught:
        await service.append_for_owner(
            uuid4(),
            uuid4(),
            MessageRole.USER,
            "content",
        )

    assert caught.value is error
    assert events == ["execute", "rollback"]
    session.rollback.assert_awaited_once_with()
    session.commit.assert_not_awaited()
    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_service_passes_expected_sequence_and_commits_guarded_append(
    monkeypatch,
):
    session = _service_session(4)
    service = MessageService(session)
    guarded_append = AsyncMock(
        return_value=Message(
            conversation_id=uuid4(),
            role=MessageRole.ASSISTANT,
            content="answer",
            sequence_number=4,
        )
    )
    monkeypatch.setattr(service.repository, "append_for_owner", guarded_append)
    owner_id = uuid4()
    conversation_id = uuid4()

    await service.append_for_owner(
        owner_id,
        conversation_id,
        MessageRole.ASSISTANT,
        "answer",
        expected_sequence_number=4,
    )

    guarded_append.assert_awaited_once_with(
        owner_id,
        conversation_id,
        MessageRole.ASSISTANT,
        "answer",
        expected_sequence_number=4,
    )
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()
