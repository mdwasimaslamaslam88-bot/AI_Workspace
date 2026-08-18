import asyncio
from types import SimpleNamespace
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
    DEFAULT_MESSAGE_PAGE_SIZE,
    GenerationContextMessage,
    GenerationContextSnapshot,
    MAX_MESSAGE_PAGE_CONTENT_CHARACTERS,
    MAX_MESSAGE_PAGE_SIZE,
    MessageCursor,
    MessagePagination,
    MessageRepository,
)


def _session_with_allocated_sequence(
    sequence_number: int | None,
    *,
    rows=(),
    page_rows=(),
):
    session = AsyncMock(spec=AsyncSession)
    result = Mock()
    result.scalar_one_or_none.return_value = sequence_number
    result.scalars.return_value.all.return_value = list(rows)
    result.all.return_value = list(page_rows)
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


def _context_row(
    *,
    role: MessageRole | None,
    content: str | None,
    sequence_number: int | None,
    candidate_count: int,
    final_sequence_number: int | None,
    oversized: bool,
):
    return SimpleNamespace(
        message_role=role,
        message_content=content,
        message_sequence_number=sequence_number,
        candidate_count=candidate_count,
        final_candidate_sequence_number=final_sequence_number,
        generation_context_oversized=oversized,
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
        page_rows=((first, 3), (second, 3)),
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
    assert sql.startswith("with message_page_candidates as")
    candidate_sql = sql.split("), ranked_message_page_candidates as", 1)[0]
    assert "select messages.id as message_id" in candidate_sql
    assert "messages.sequence_number as sequence_number" in candidate_sql
    assert "char_length(messages.content) as content_characters" in candidate_sql
    assert "messages.role" not in candidate_sql
    assert "messages.created_at" not in candidate_sql
    assert "messages.updated_at" not in candidate_sql
    assert "join conversations on conversations.id = messages.conversation_id" in sql
    assert sql.count("conversations.owner_id =") == 2
    assert sql.count("conversations.id =") == 4
    assert sql.count("messages.conversation_id =") == 2
    assert "sum(message_page_candidates.content_characters) over" in sql
    assert "rows between unbounded preceding and current row" in sql
    assert "ranked_message_page_candidates.cumulative_content_characters" in sql
    assert "join selected_message_page_candidates" in sql
    assert "select messages.id, messages.conversation_id" in sql
    assert "order by messages.sequence_number asc" in sql
    assert "offset" not in sql
    assert compiled.params["message_candidate_limit"] == 3
    assert compiled.params["message_page_limit"] == 2
    assert compiled.params["message_page_content_character_limit"] == (
        MAX_MESSAGE_PAGE_CONTENT_CHARACTERS
    )
    assert "message_cursor_sequence_number" not in compiled.params
    assert owner_id in compiled.params.values()
    assert list(compiled.params.values()).count(owner_id) == 2
    assert list(compiled.params.values()).count(conversation_id) == 4
    session.execute.assert_awaited_once()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_next_message_page_uses_strict_sequence_cursor():
    owner_id = uuid4()
    conversation_id = uuid4()
    remaining = _message(3)
    cursor = MessageCursor(sequence_number=2)
    session = _session_with_allocated_sequence(
        None,
        page_rows=((remaining, 1),),
    )
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
    assert compiled.params["message_candidate_limit"] == 3
    assert compiled.params["message_page_limit"] == 2
    assert compiled.params["message_page_content_character_limit"] == (
        MAX_MESSAGE_PAGE_CONTENT_CHARACTERS
    )
    assert owner_id in compiled.params.values()
    assert list(compiled.params.values()).count(owner_id) == 2
    assert list(compiled.params.values()).count(conversation_id) == 4
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
    assert compiled.params["message_candidate_limit"] == MAX_MESSAGE_PAGE_SIZE + 1
    assert compiled.params["message_page_limit"] == MAX_MESSAGE_PAGE_SIZE
    assert compiled.params["message_page_content_character_limit"] == (
        MAX_MESSAGE_PAGE_CONTENT_CHARACTERS
    )
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_default_message_page_size_limits_candidate_inspection():
    session = _session_with_allocated_sequence(None)

    await MessageRepository(session).list_for_owner(uuid4(), uuid4())

    statement = session.execute.await_args.args[0]
    compiled, _sql = _compile(statement)
    assert compiled.params["message_candidate_limit"] == (
        DEFAULT_MESSAGE_PAGE_SIZE + 1
    )
    assert compiled.params["message_page_limit"] == DEFAULT_MESSAGE_PAGE_SIZE


@pytest.mark.asyncio
async def test_exact_content_budget_message_is_returned_intact():
    message = Message(
        conversation_id=uuid4(),
        role=MessageRole.USER,
        content="é" * MAX_MESSAGE_PAGE_CONTENT_CHARACTERS,
        sequence_number=1,
    )
    session = _session_with_allocated_sequence(
        None,
        page_rows=((message, 1),),
    )

    page = await MessageRepository(session).list_for_owner(
        uuid4(),
        message.conversation_id,
        MessagePagination(limit=100),
    )

    assert page.items == (message,)
    assert page.items[0].content == message.content
    assert len(page.items[0].content) == MAX_MESSAGE_PAGE_CONTENT_CHARACTERS
    assert page.next_cursor is None


@pytest.mark.asyncio
async def test_content_budget_defers_next_whole_message_with_cursor():
    conversation_id = uuid4()
    first = Message(
        id=uuid4(),
        conversation_id=conversation_id,
        role=MessageRole.USER,
        content="a" * 60_000,
        sequence_number=1,
    )
    excluded = Message(
        conversation_id=conversation_id,
        role=MessageRole.ASSISTANT,
        content="b" * 60_000,
        sequence_number=2,
    )
    session = _session_with_allocated_sequence(
        None,
        page_rows=((first, 2),),
    )

    page = await MessageRepository(session).list_for_owner(
        uuid4(),
        conversation_id,
        MessagePagination(limit=100),
    )

    assert page.items == (first,)
    assert excluded not in page.items
    assert page.next_cursor == MessageCursor(sequence_number=1)


@pytest.mark.asyncio
async def test_cumulative_content_exactly_at_budget_accepts_full_prefix():
    conversation_id = uuid4()
    first = Message(
        conversation_id=conversation_id,
        role=MessageRole.USER,
        content="a" * 40_000,
        sequence_number=1,
    )
    second = Message(
        id=uuid4(),
        conversation_id=conversation_id,
        role=MessageRole.ASSISTANT,
        content="b" * 60_000,
        sequence_number=2,
    )
    session = _session_with_allocated_sequence(
        None,
        page_rows=((first, 2), (second, 2)),
    )

    page = await MessageRepository(session).list_for_owner(
        uuid4(),
        conversation_id,
        MessagePagination(limit=100),
    )

    assert page.items == (first, second)
    assert sum(len(message.content) for message in page.items) == (
        MAX_MESSAGE_PAGE_CONTENT_CHARACTERS
    )
    assert page.next_cursor is None


@pytest.mark.asyncio
async def test_cumulative_content_over_budget_excludes_complete_next_message():
    conversation_id = uuid4()
    first = Message(
        conversation_id=conversation_id,
        role=MessageRole.USER,
        content="a" * 60_000,
        sequence_number=1,
    )
    session = _session_with_allocated_sequence(
        None,
        page_rows=((first, 2),),
    )

    page = await MessageRepository(session).list_for_owner(
        uuid4(),
        conversation_id,
        MessagePagination(limit=100),
    )

    assert sum(len(message.content) for message in page.items) == 60_000
    assert MAX_MESSAGE_PAGE_CONTENT_CHARACTERS - 60_000 == 40_000
    assert page.next_cursor == MessageCursor(sequence_number=1)


@pytest.mark.asyncio
async def test_content_budget_cursor_traversal_has_no_gaps_or_duplicates():
    conversation_id = uuid4()
    first = Message(
        id=uuid4(),
        conversation_id=conversation_id,
        role=MessageRole.USER,
        content="a" * 60_000,
        sequence_number=1,
    )
    second = Message(
        id=uuid4(),
        conversation_id=conversation_id,
        role=MessageRole.ASSISTANT,
        content="b" * 60_000,
        sequence_number=2,
    )
    first_result = Mock()
    first_result.all.return_value = [(first, 2)]
    second_result = Mock()
    second_result.all.return_value = [(second, 1)]
    session = AsyncMock(spec=AsyncSession)
    session.execute.side_effect = [first_result, second_result]
    repository = MessageRepository(session)
    owner_id = uuid4()

    first_page = await repository.list_for_owner(
        owner_id,
        conversation_id,
        MessagePagination(limit=100),
    )
    second_page = await repository.list_for_owner(
        owner_id,
        conversation_id,
        MessagePagination(limit=100, cursor=first_page.next_cursor),
    )

    assert first_page.next_cursor == MessageCursor(sequence_number=1)
    assert second_page.next_cursor is None
    assert first_page.items + second_page.items == (first, second)
    assert len({message.id for message in first_page.items + second_page.items}) == 2
    second_statement = session.execute.await_args_list[1].args[0]
    compiled, sql = _compile(second_statement)
    assert "messages.sequence_number >" in sql
    assert compiled.params["message_cursor_sequence_number"] == 1


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
    rows = (
        _context_row(
            role=MessageRole.SYSTEM,
            content="system",
            sequence_number=1,
            candidate_count=2,
            final_sequence_number=2,
            oversized=False,
        ),
        _context_row(
            role=MessageRole.USER,
            content="question",
            sequence_number=2,
            candidate_count=2,
            final_sequence_number=2,
            oversized=False,
        ),
    )
    session = _session_with_allocated_sequence(None, page_rows=rows)

    snapshot = await MessageRepository(
        session
    ).list_generation_context_for_owner(
        owner_id,
        conversation_id,
        max_messages=100,
        max_context_characters=100_000,
    )

    assert snapshot == GenerationContextSnapshot(
        messages=(
            GenerationContextMessage(MessageRole.SYSTEM, "system", 1),
            GenerationContextMessage(MessageRole.USER, "question", 2),
        ),
        candidate_count=2,
        final_sequence_number=2,
        oversized=False,
    )
    assert all(not isinstance(item, Message) for item in snapshot.messages)
    statement = session.execute.await_args.args[0]
    assert isinstance(statement, Select)
    compiled, sql = _compile(statement)
    assert sql.startswith("with generation_context_candidates as")
    candidate_sql = sql.split("), generation_context_metadata as", 1)[0]
    assert "select messages.id as message_id" in candidate_sql
    assert "messages.sequence_number as sequence_number" in candidate_sql
    assert "char_length(messages.content) as content_characters" in candidate_sql
    assert "messages.role" not in candidate_sql
    assert "messages.created_at" not in candidate_sql
    assert "messages.updated_at" not in candidate_sql
    assert "messages.conversation_id as" not in candidate_sql
    assert "count(*) as candidate_count" in sql
    assert "sum(generation_context_candidates.content_characters)" in sql
    assert "max(generation_context_candidates.sequence_number)" in sql
    assert "projected_generation_context_messages.message_role" in sql
    assert "projected_generation_context_messages.message_content" in sql
    assert "projected_generation_context_messages.message_sequence_number" in sql
    assert "messages.id, messages.conversation_id" not in sql
    assert "messages.created_at" not in sql
    assert "messages.updated_at" not in sql
    assert sql.count("conversations.owner_id =") == 2
    assert sql.count("messages.conversation_id =") == 2
    assert "order by projected_generation_context_messages.message_sequence_number asc" in sql
    assert "offset" not in sql
    assert "cursor" not in sql
    assert "message_page_candidates" not in sql
    assert compiled.params["generation_context_fetch_limit"] == 101
    assert compiled.params["generation_context_message_limit"] == 100
    assert compiled.params["generation_context_character_limit"] == 100_000
    assert owner_id in compiled.params.values()
    assert conversation_id in compiled.params.values()
    session.execute.assert_awaited_once_with(statement)
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_generation_context_empty_metadata_returns_no_messages():
    session = _session_with_allocated_sequence(
        None,
        page_rows=(
            _context_row(
                role=None,
                content=None,
                sequence_number=None,
                candidate_count=0,
                final_sequence_number=None,
                oversized=False,
            ),
        ),
    )

    snapshot = await MessageRepository(
        session
    ).list_generation_context_for_owner(
        uuid4(),
        uuid4(),
        max_messages=100,
        max_context_characters=100_000,
    )

    assert snapshot == GenerationContextSnapshot((), 0, None, False)
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_generation_context_accepts_exact_message_and_character_limits():
    rows = tuple(
        _context_row(
            role=MessageRole.USER,
            content=("界" * 99_901 if sequence == 100 else "x"),
            sequence_number=sequence,
            candidate_count=100,
            final_sequence_number=100,
            oversized=False,
        )
        for sequence in range(1, 101)
    )
    session = _session_with_allocated_sequence(None, page_rows=rows)

    snapshot = await MessageRepository(
        session
    ).list_generation_context_for_owner(
        uuid4(),
        uuid4(),
        max_messages=100,
        max_context_characters=100_000,
    )

    assert len(snapshot.messages) == 100
    assert sum(len(item.content) for item in snapshot.messages) == 100_000
    assert snapshot.candidate_count == 100
    assert snapshot.final_sequence_number == 100
    assert snapshot.oversized is False
    assert snapshot.messages[-1].content == "界" * 99_901


@pytest.mark.asyncio
async def test_generation_context_message_overflow_returns_metadata_only():
    session = _session_with_allocated_sequence(
        None,
        page_rows=(
            _context_row(
                role=None,
                content=None,
                sequence_number=None,
                candidate_count=101,
                final_sequence_number=101,
                oversized=True,
            ),
        ),
    )

    snapshot = await MessageRepository(
        session
    ).list_generation_context_for_owner(
        uuid4(),
        uuid4(),
        max_messages=100,
        max_context_characters=100_000,
    )

    assert snapshot.messages == ()
    assert snapshot.candidate_count == 101
    assert snapshot.final_sequence_number == 101
    assert snapshot.oversized is True
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("candidate_count", "final_sequence_number"),
    [(2, 2), (1, 1)],
)
async def test_generation_context_character_overflow_returns_metadata_only(
    candidate_count,
    final_sequence_number,
):
    session = _session_with_allocated_sequence(
        None,
        page_rows=(
            _context_row(
                role=None,
                content=None,
                sequence_number=None,
                candidate_count=candidate_count,
                final_sequence_number=final_sequence_number,
                oversized=True,
            ),
        ),
    )

    snapshot = await MessageRepository(
        session
    ).list_generation_context_for_owner(
        uuid4(),
        uuid4(),
        max_messages=100,
        max_context_characters=100_000,
    )

    assert snapshot == GenerationContextSnapshot(
        messages=(),
        candidate_count=candidate_count,
        final_sequence_number=final_sequence_number,
        oversized=True,
    )
    session.execute.assert_awaited_once()


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
            max_context_characters=100_000,
        )

    session.execute.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.parametrize(
    "max_context_characters",
    [True, 1.5, "100000", 0, -1],
)
@pytest.mark.asyncio
async def test_generation_context_rejects_invalid_character_bound_before_querying(
    max_context_characters,
):
    session = _session_with_allocated_sequence(None)

    with pytest.raises((TypeError, ValueError)):
        await MessageRepository(
            session
        ).list_generation_context_for_owner(
            uuid4(),
            uuid4(),
            max_messages=100,
            max_context_characters=max_context_characters,
        )

    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_generation_context_query_cancellation_propagates():
    session = _session_with_allocated_sequence(None)
    session.execute.side_effect = asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await MessageRepository(
            session
        ).list_generation_context_for_owner(
            uuid4(),
            uuid4(),
            max_messages=100,
            max_context_characters=100_000,
        )

    session.execute.assert_awaited_once()


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
