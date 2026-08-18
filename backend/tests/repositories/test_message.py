from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import Update
from sqlalchemy.sql.selectable import Select

from app.models import Message, MessageRole
from app.models.message import (
    MAX_MESSAGE_CONTENT_CHARACTERS,
    MessageContentTooLargeError,
)
from app.repositories.message import (
    MAX_MESSAGE_PAGE_SIZE,
    MessageCursor,
    MessagePagination,
    MessageRepository,
)


def _session_with_allocated_sequence(sequence_number: int | None, *, rows=()):
    session = AsyncMock(spec=AsyncSession)
    result = Mock()
    result.scalar_one_or_none.return_value = sequence_number
    result.scalars.return_value.all.return_value = list(rows)
    session.execute.return_value = result
    return session


def _compile(statement):
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = " ".join(str(compiled).split()).lower()
    return compiled, sql


def _message(sequence_number: int) -> Message:
    return Message(
        conversation_id=uuid4(),
        role=MessageRole.USER,
        content=f"message {sequence_number}",
        sequence_number=sequence_number,
    )


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
async def test_append_accepts_exact_message_content_character_boundary():
    session = _session_with_allocated_sequence(1)
    repository = MessageRepository(session)
    content = "é" * MAX_MESSAGE_CONTENT_CHARACTERS

    message = await repository.append_for_owner(
        uuid4(),
        uuid4(),
        MessageRole.USER,
        content,
    )

    assert message is not None
    assert message.content == content
    session.execute.assert_awaited_once()
    session.add.assert_called_once_with(message)
    session.flush.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_append_rejects_oversized_content_before_sequence_allocation():
    session = _session_with_allocated_sequence(1)
    repository = MessageRepository(session)

    with pytest.raises(MessageContentTooLargeError) as captured:
        await repository.append_for_owner(
            uuid4(),
            uuid4(),
            MessageRole.ASSISTANT,
            "x" * (MAX_MESSAGE_CONTENT_CHARACTERS + 1),
        )

    assert str(captured.value) == "persisted text is too large"
    session.execute.assert_not_awaited()
    session.add.assert_not_called()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


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


@pytest.mark.asyncio
async def test_first_message_page_is_owner_scoped_bounded_and_sequence_ordered():
    owner_id = uuid4()
    conversation_id = uuid4()
    first = _message(1)
    second = _message(2)
    extra = _message(3)
    session = _session_with_allocated_sequence(
        None,
        rows=(first, second, extra),
    )
    repository = MessageRepository(session)

    page = await repository.list_for_owner(
        owner_id,
        conversation_id,
        MessagePagination(limit=2),
    )

    assert page.items == (first, second)
    assert page.next_cursor == MessageCursor(sequence_number=2)
    statement = session.execute.await_args.args[0]
    assert isinstance(statement, Select)
    compiled, sql = _compile(statement)
    assert sql.startswith("select messages.id, messages.conversation_id")
    assert "join conversations on conversations.id = messages.conversation_id" in sql
    assert "where conversations.owner_id =" in sql
    assert "and conversations.id =" in sql
    assert "and messages.conversation_id =" in sql
    assert "order by messages.sequence_number asc" in sql
    assert "offset" not in sql
    assert compiled.params["message_fetch_limit"] == 3
    assert "message_cursor_sequence_number" not in compiled.params
    assert owner_id in compiled.params.values()
    assert list(compiled.params.values()).count(conversation_id) == 2
    session.execute.assert_awaited_once()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_next_message_page_uses_strict_sequence_cursor():
    owner_id = uuid4()
    conversation_id = uuid4()
    remaining = _message(3)
    cursor = MessageCursor(sequence_number=2)
    session = _session_with_allocated_sequence(None, rows=(remaining,))
    repository = MessageRepository(session)

    page = await repository.list_for_owner(
        owner_id,
        conversation_id,
        MessagePagination(limit=2, cursor=cursor),
    )

    assert page.items == (remaining,)
    assert page.next_cursor is None
    statement = session.execute.await_args.args[0]
    compiled, sql = _compile(statement)
    assert "messages.sequence_number >" in sql
    assert "order by messages.sequence_number asc" in sql
    assert compiled.params["message_cursor_sequence_number"] == 2
    assert compiled.params["message_fetch_limit"] == 3
    assert owner_id in compiled.params.values()
    assert list(compiled.params.values()).count(conversation_id) == 2
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("ownership_case", ["missing", "different-owner"])
async def test_message_list_miss_is_consistently_empty(ownership_case):
    session = _session_with_allocated_sequence(None, rows=())
    repository = MessageRepository(session)

    page = await repository.list_for_owner(uuid4(), uuid4())

    assert ownership_case in {"missing", "different-owner"}
    assert page.items == ()
    assert page.next_cursor is None
    session.execute.assert_awaited_once()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_maximum_message_page_size_remains_bounded():
    session = _session_with_allocated_sequence(None)
    repository = MessageRepository(session)

    await repository.list_for_owner(
        uuid4(),
        uuid4(),
        MessagePagination(limit=MAX_MESSAGE_PAGE_SIZE),
    )

    statement = session.execute.await_args.args[0]
    compiled, _sql = _compile(statement)
    assert compiled.params["message_fetch_limit"] == MAX_MESSAGE_PAGE_SIZE + 1
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.parametrize("limit", [0, -1, MAX_MESSAGE_PAGE_SIZE + 1])
def test_message_pagination_rejects_out_of_bounds_limits(limit):
    with pytest.raises(ValueError, match="message pagination limit"):
        MessagePagination(limit=limit)


@pytest.mark.parametrize("limit", [True, 1.5, "10"])
def test_message_pagination_rejects_non_integer_limits(limit):
    with pytest.raises(TypeError, match="message pagination limit"):
        MessagePagination(limit=limit)


@pytest.mark.parametrize("sequence_number", [0, -1])
def test_message_cursor_rejects_non_positive_values(sequence_number):
    with pytest.raises(ValueError, match="must be positive"):
        MessageCursor(sequence_number=sequence_number)


@pytest.mark.parametrize("sequence_number", [True, 1.5, "1"])
def test_message_cursor_rejects_non_integer_values(sequence_number):
    with pytest.raises(TypeError, match="must be an integer"):
        MessageCursor(sequence_number=sequence_number)


def test_message_pagination_rejects_wrong_cursor_type():
    with pytest.raises(TypeError, match="must be a MessageCursor"):
        MessagePagination(cursor=object())


@pytest.mark.asyncio
async def test_message_repository_rejects_wrong_pagination_before_querying():
    session = _session_with_allocated_sequence(None)
    repository = MessageRepository(session)

    with pytest.raises(TypeError, match="pagination"):
        await repository.list_for_owner(uuid4(), uuid4(), object())

    session.execute.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_generation_context_is_owner_scoped_ordered_and_bounded_without_cursor():
    owner_id = uuid4()
    conversation_id = uuid4()
    rows = (_message(1), _message(2))
    session = _session_with_allocated_sequence(None, rows=rows)

    context = await MessageRepository(
        session
    ).list_generation_context_for_owner(
        owner_id,
        conversation_id,
        max_messages=100,
    )

    assert context == rows
    statement = session.execute.await_args.args[0]
    assert isinstance(statement, Select)
    compiled, sql = _compile(statement)
    assert "join conversations" in sql
    assert "conversations.owner_id =" in sql
    assert "conversations.id =" in sql
    assert "messages.conversation_id =" in sql
    assert "order by messages.sequence_number asc" in sql
    assert "offset" not in sql
    assert "cursor" not in sql
    assert compiled.params["generation_context_fetch_limit"] == 101
    assert owner_id in compiled.params.values()
    assert conversation_id in compiled.params.values()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.parametrize("max_messages", [True, 1.5, "100", 0, -1])
@pytest.mark.asyncio
async def test_generation_context_rejects_invalid_bound_before_querying(
    max_messages,
):
    session = _session_with_allocated_sequence(None)

    with pytest.raises((TypeError, ValueError)):
        await MessageRepository(
            session
        ).list_generation_context_for_owner(
            uuid4(),
            uuid4(),
            max_messages=max_messages,
        )

    session.execute.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_expected_sequence_adds_atomic_compare_and_append_condition():
    owner_id = uuid4()
    conversation_id = uuid4()
    session = _session_with_allocated_sequence(7)
    repository = MessageRepository(session)

    message = await repository.append_for_owner(
        owner_id,
        conversation_id,
        MessageRole.ASSISTANT,
        "generated answer",
        expected_sequence_number=7,
    )

    assert message is not None
    statement = session.execute.await_args.args[0]
    compiled, sql = _compile(statement)
    assert "and conversations.next_message_sequence =" in sql
    assert owner_id in compiled.params.values()
    assert conversation_id in compiled.params.values()
    assert 7 in compiled.params.values()
    session.add.assert_called_once_with(message)
    session.flush.assert_awaited_once_with()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_expected_sequence_mismatch_returns_owner_scoped_miss():
    session = _session_with_allocated_sequence(None)
    repository = MessageRepository(session)

    message = await repository.append_for_owner(
        uuid4(),
        uuid4(),
        MessageRole.ASSISTANT,
        "stale answer",
        expected_sequence_number=2,
    )

    assert message is None
    session.execute.assert_awaited_once()
    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.parametrize("expected", [True, 1.5, "2", 0, -1])
@pytest.mark.asyncio
async def test_expected_sequence_validation_precedes_database_work(expected):
    session = _session_with_allocated_sequence(None)

    with pytest.raises((TypeError, ValueError)):
        await MessageRepository(session).append_for_owner(
            uuid4(),
            uuid4(),
            MessageRole.ASSISTANT,
            "answer",
            expected_sequence_number=expected,
        )

    session.execute.assert_not_awaited()
