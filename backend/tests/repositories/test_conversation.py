from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import Delete, Update
from sqlalchemy.sql.selectable import Select

from app.models import Conversation
from app.repositories.conversation import (
    MAX_CONVERSATION_PAGE_SIZE,
    ConversationCursor,
    ConversationPagination,
    ConversationRepository,
)


def _session_with_result(*, scalar=None, rows=()):
    session = AsyncMock(spec=AsyncSession)
    result = Mock()
    result.scalar_one_or_none.return_value = scalar
    result.scalars.return_value.all.return_value = list(rows)
    session.execute.return_value = result
    return session


def _compile(statement):
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = " ".join(str(compiled).split()).lower()
    return compiled, sql


def _conversation(*, updated_at: datetime | None = None) -> Conversation:
    return Conversation(
        id=uuid4(),
        owner_id=uuid4(),
        title=None,
        updated_at=updated_at or datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("title", [None, "  Exact title  "])
async def test_create_preserves_owner_and_title_and_flushes_without_commit(title):
    owner_id = uuid4()
    session = _session_with_result()
    repository = ConversationRepository(session)

    conversation = await repository.create(owner_id, title)

    assert conversation.owner_id == owner_id
    assert conversation.title == title
    session.add.assert_called_once_with(conversation)
    session.flush.assert_awaited_once_with()
    session.execute.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_is_owner_and_conversation_scoped_and_returns_miss():
    owner_id = uuid4()
    conversation_id = uuid4()
    session = _session_with_result(scalar=None)
    repository = ConversationRepository(session)

    conversation = await repository.get_for_owner(owner_id, conversation_id)

    assert conversation is None
    statement = session.execute.await_args.args[0]
    assert isinstance(statement, Select)
    compiled, sql = _compile(statement)
    assert "where conversations.owner_id =" in sql
    assert "and conversations.id =" in sql
    assert "join" not in sql
    assert "messages" not in sql
    assert owner_id in compiled.params.values()
    assert conversation_id in compiled.params.values()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_first_page_is_owner_scoped_bounded_and_deterministically_ordered():
    owner_id = uuid4()
    newest = _conversation(updated_at=datetime(2026, 8, 3, tzinfo=timezone.utc))
    second = _conversation(updated_at=datetime(2026, 8, 2, tzinfo=timezone.utc))
    extra = _conversation(updated_at=datetime(2026, 8, 1, tzinfo=timezone.utc))
    session = _session_with_result(rows=(newest, second, extra))
    repository = ConversationRepository(session)

    page = await repository.list_for_owner(
        owner_id,
        ConversationPagination(limit=2),
    )

    assert page.items == (newest, second)
    assert page.next_cursor == ConversationCursor(
        updated_at=second.updated_at,
        id=second.id,
    )

    statement = session.execute.await_args.args[0]
    assert isinstance(statement, Select)
    compiled, sql = _compile(statement)
    assert "where conversations.owner_id =" in sql
    assert "conversations.is_archived is false" in sql
    assert (
        "order by conversations.updated_at desc, conversations.id desc" in sql
    )
    assert compiled.params["fetch_limit"] == 3
    assert "cursor_updated_at" not in compiled.params
    assert owner_id in compiled.params.values()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_next_page_uses_matching_updated_at_and_id_keyset_cursor():
    owner_id = uuid4()
    cursor = ConversationCursor(
        updated_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
        id=uuid4(),
    )
    remaining = _conversation(updated_at=datetime(2026, 8, 1, tzinfo=timezone.utc))
    session = _session_with_result(rows=(remaining,))
    repository = ConversationRepository(session)

    page = await repository.list_for_owner(
        owner_id,
        ConversationPagination(limit=2, cursor=cursor),
    )

    assert page.items == (remaining,)
    assert page.next_cursor is None

    statement = session.execute.await_args.args[0]
    compiled, sql = _compile(statement)
    assert "where conversations.owner_id =" in sql
    assert "(conversations.updated_at, conversations.id) <" in sql
    assert (
        "order by conversations.updated_at desc, conversations.id desc" in sql
    )
    assert compiled.params["cursor_updated_at"] == cursor.updated_at
    assert compiled.params["cursor_id"] == cursor.id
    assert compiled.params["fetch_limit"] == 3
    assert owner_id in compiled.params.values()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_maximum_page_size_remains_bounded():
    owner_id = uuid4()
    session = _session_with_result()
    repository = ConversationRepository(session)

    await repository.list_for_owner(
        owner_id,
        ConversationPagination(limit=MAX_CONVERSATION_PAGE_SIZE),
    )

    statement = session.execute.await_args.args[0]
    compiled, _sql = _compile(statement)
    assert compiled.params["fetch_limit"] == MAX_CONVERSATION_PAGE_SIZE + 1
    session.commit.assert_not_awaited()


@pytest.mark.parametrize("limit", [0, -1, MAX_CONVERSATION_PAGE_SIZE + 1])
def test_pagination_rejects_out_of_bounds_limits(limit):
    with pytest.raises(ValueError, match="pagination limit"):
        ConversationPagination(limit=limit)


@pytest.mark.parametrize("limit", [True, 1.5, "10"])
def test_pagination_rejects_non_integer_limits(limit):
    with pytest.raises(TypeError, match="pagination limit"):
        ConversationPagination(limit=limit)


def test_pagination_rejects_non_boolean_archive_filter():
    with pytest.raises(TypeError, match="include_archived"):
        ConversationPagination(include_archived=1)


@pytest.mark.asyncio
async def test_archived_inclusion_is_explicit_and_owner_scoped():
    owner_id = uuid4()
    session = _session_with_result()

    await ConversationRepository(session).list_for_owner(
        owner_id,
        ConversationPagination(include_archived=True),
    )

    statement = session.execute.await_args.args[0]
    compiled, sql = _compile(statement)
    assert "where conversations.owner_id =" in sql
    assert "conversations.is_archived is false" not in sql
    assert owner_id in compiled.params.values()


@pytest.mark.asyncio
async def test_search_is_owner_scoped_bounded_and_matches_title_or_message():
    owner_id = uuid4()
    matched = _conversation()
    session = _session_with_result(rows=(matched,))

    page = await ConversationRepository(session).list_for_owner(
        owner_id,
        ConversationPagination(limit=10, search=r"GPU%_\Plan"),
    )

    assert page.items == (matched,)
    statement = session.execute.await_args.args[0]
    compiled, sql = _compile(statement)
    assert "where conversations.owner_id =" in sql
    assert "conversations.is_archived is false" in sql
    assert "lower(conversations.title) like" in sql
    assert "exists (select messages.id" in sql
    assert "messages.conversation_id = conversations.id" in sql
    assert "lower(messages.content) like" in sql
    assert compiled.params["conversation_search_pattern"] == r"%gpu\%\_\\plan%"
    assert compiled.params["fetch_limit"] == 11
    assert owner_id in compiled.params.values()


@pytest.mark.parametrize("search", ["", "   ", "x" * 501])
def test_search_rejects_empty_or_oversized_values(search):
    with pytest.raises(ValueError, match="conversation search"):
        ConversationPagination(search=search)


def test_search_rejects_non_string_values():
    with pytest.raises(TypeError, match="conversation search"):
        ConversationPagination(search=1)


def test_cursor_rejects_malformed_values():
    with pytest.raises(ValueError, match="timezone-aware"):
        ConversationCursor(updated_at=datetime(2026, 8, 1), id=uuid4())

    with pytest.raises(TypeError, match="updated_at"):
        ConversationCursor(updated_at="2026-08-01", id=uuid4())

    with pytest.raises(TypeError, match="cursor id"):
        ConversationCursor(
            updated_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            id="not-a-uuid",
        )

    with pytest.raises(TypeError, match="pagination cursor"):
        ConversationPagination(cursor=object())


@pytest.mark.asyncio
async def test_repository_rejects_wrong_pagination_type_before_querying():
    session = _session_with_result()
    repository = ConversationRepository(session)

    with pytest.raises(TypeError, match="pagination"):
        await repository.list_for_owner(uuid4(), object())

    session.execute.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_rename_is_owner_scoped_preserves_title_and_returns_entity():
    owner_id = uuid4()
    conversation_id = uuid4()
    updated = _conversation()
    session = _session_with_result(scalar=updated)
    repository = ConversationRepository(session)

    conversation = await repository.rename_for_owner(
        owner_id,
        conversation_id,
        "  Renamed exactly  ",
    )

    assert conversation is updated
    statement = session.execute.await_args.args[0]
    assert isinstance(statement, Update)
    compiled, sql = _compile(statement)
    assert "update conversations set" in sql
    assert "where conversations.owner_id =" in sql
    assert "updated_at=now()" in sql
    assert "and conversations.id =" in sql
    assert "returning conversations.id" in sql
    assert "  Renamed exactly  " in compiled.params.values()
    assert owner_id in compiled.params.values()
    assert conversation_id in compiled.params.values()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_state_update_is_owner_scoped_and_returns_entity():
    owner_id = uuid4()
    conversation_id = uuid4()
    updated = _conversation()
    session = _session_with_result(scalar=updated)

    conversation = await ConversationRepository(session).set_state_for_owner(
        owner_id,
        conversation_id,
        is_pinned=True,
        is_archived=False,
    )

    assert conversation is updated
    statement = session.execute.await_args.args[0]
    assert isinstance(statement, Update)
    compiled, sql = _compile(statement)
    assert "update conversations set updated_at=now(), is_pinned=" in sql
    assert "is_archived=" in sql
    assert "where conversations.owner_id =" in sql
    assert "and conversations.id =" in sql
    assert "returning conversations.id" in sql
    assert owner_id in compiled.params.values()
    assert conversation_id in compiled.params.values()


@pytest.mark.parametrize(
    "kwargs",
    [{}, {"is_pinned": 1}, {"is_archived": "yes"}],
)
@pytest.mark.asyncio
async def test_state_update_rejects_missing_or_non_boolean_values(kwargs):
    session = _session_with_result()

    with pytest.raises((TypeError, ValueError)):
        await ConversationRepository(session).set_state_for_owner(
            uuid4(),
            uuid4(),
            **kwargs,
        )

    session.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("ownership_case", ["missing", "different-owner"])
async def test_rename_miss_is_consistent(ownership_case):
    session = _session_with_result(scalar=None)
    repository = ConversationRepository(session)

    conversation = await repository.rename_for_owner(
        uuid4(),
        uuid4(),
        ownership_case,
    )

    assert conversation is None
    session.execute.assert_awaited_once()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_is_owner_scoped_and_does_not_load_messages():
    owner_id = uuid4()
    conversation_id = uuid4()
    session = _session_with_result(scalar=conversation_id)
    repository = ConversationRepository(session)

    deleted = await repository.delete_for_owner(owner_id, conversation_id)

    assert deleted is True
    statement = session.execute.await_args.args[0]
    assert isinstance(statement, Delete)
    compiled, sql = _compile(statement)
    assert sql.startswith("delete from conversations")
    assert "where conversations.owner_id =" in sql
    assert "and conversations.id =" in sql
    assert "returning conversations.id" in sql
    assert "select" not in sql
    assert "messages" not in sql
    assert owner_id in compiled.params.values()
    assert conversation_id in compiled.params.values()
    session.execute.assert_awaited_once()
    session.add.assert_not_called()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("ownership_case", ["missing", "different-owner"])
async def test_delete_miss_is_consistent(ownership_case):
    session = _session_with_result(scalar=None)
    repository = ConversationRepository(session)

    deleted = await repository.delete_for_owner(uuid4(), uuid4())

    assert ownership_case in {"missing", "different-owner"}
    assert deleted is False
    session.execute.assert_awaited_once()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()
