from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation
from app.repositories.conversation import ConversationPagination
from app.services.conversation import ConversationService


def _service_session(*, scalar=None, rows=()):
    session = AsyncMock(spec=AsyncSession)
    result = Mock()
    result.scalar_one_or_none.return_value = scalar
    result.scalars.return_value.all.return_value = list(rows)
    session.execute.return_value = result
    return session


@pytest.mark.asyncio
async def test_successful_create_orders_add_flush_commit():
    events: list[str] = []
    session = _service_session()

    async def flush():
        events.append("flush")

    async def commit():
        events.append("commit")

    session.add.side_effect = lambda _conversation: events.append("add")
    session.flush.side_effect = flush
    session.commit.side_effect = commit
    service = ConversationService(session)
    owner_id = uuid4()

    conversation = await service.create(owner_id, "Title")

    assert conversation.owner_id == owner_id
    assert conversation.title == "Title"
    assert events == ["add", "flush", "commit"]
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_successful_rename_orders_execute_commit():
    events: list[str] = []
    updated = Conversation(id=uuid4(), owner_id=uuid4(), title="Renamed")
    session = _service_session(scalar=updated)
    result = session.execute.return_value

    async def execute(_statement):
        events.append("execute")
        return result

    async def commit():
        events.append("commit")

    session.execute.side_effect = execute
    session.commit.side_effect = commit
    service = ConversationService(session)

    conversation = await service.rename_for_owner(
        updated.owner_id,
        updated.id,
        "Renamed",
    )

    assert conversation is updated
    assert events == ["execute", "commit"]
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_successful_delete_orders_execute_commit():
    events: list[str] = []
    conversation_id = uuid4()
    session = _service_session(scalar=conversation_id)
    result = session.execute.return_value

    async def execute(_statement):
        events.append("execute")
        return result

    async def commit():
        events.append("commit")

    session.execute.side_effect = execute
    session.commit.side_effect = commit
    service = ConversationService(session)

    deleted = await service.delete_for_owner(uuid4(), conversation_id)

    assert deleted is True
    assert events == ["execute", "commit"]
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_and_list_never_commit():
    existing = Conversation(id=uuid4(), owner_id=uuid4(), title=None)
    session = _service_session(scalar=existing, rows=(existing,))
    service = ConversationService(session)

    conversation = await service.get_for_owner(existing.owner_id, existing.id)
    page = await service.list_for_owner(
        existing.owner_id,
        ConversationPagination(limit=1),
    )

    assert conversation is existing
    assert page.items == (existing,)
    assert page.next_cursor is None
    assert session.execute.await_count == 2
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_rename_rolls_back_without_commit():
    events: list[str] = []
    session = _service_session(scalar=None)

    async def rollback():
        events.append("rollback")

    session.rollback.side_effect = rollback
    service = ConversationService(session)

    conversation = await service.rename_for_owner(uuid4(), uuid4(), None)

    assert conversation is None
    assert events == ["rollback"]
    session.rollback.assert_awaited_once_with()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_delete_rolls_back_without_commit():
    events: list[str] = []
    session = _service_session(scalar=None)

    async def rollback():
        events.append("rollback")

    session.rollback.side_effect = rollback
    service = ConversationService(session)

    deleted = await service.delete_for_owner(uuid4(), uuid4())

    assert deleted is False
    assert events == ["rollback"]
    session.rollback.assert_awaited_once_with()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_flush_failure_rolls_back_and_preserves_original_exception():
    events: list[str] = []
    session = _service_session()
    error = RuntimeError("flush failed")

    async def flush():
        events.append("flush")
        raise error

    async def rollback():
        events.append("rollback")

    session.add.side_effect = lambda _conversation: events.append("add")
    session.flush.side_effect = flush
    session.rollback.side_effect = rollback
    service = ConversationService(session)

    with pytest.raises(RuntimeError) as caught:
        await service.create(uuid4(), "Title")

    assert caught.value is error
    assert events == ["add", "flush", "rollback"]
    session.rollback.assert_awaited_once_with()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_failure_rolls_back_and_preserves_integrity_error():
    events: list[str] = []
    session = _service_session()
    error = IntegrityError("rename conversation", {}, RuntimeError("constraint"))

    async def execute(_statement):
        events.append("execute")
        raise error

    async def rollback():
        events.append("rollback")

    session.execute.side_effect = execute
    session.rollback.side_effect = rollback
    service = ConversationService(session)

    with pytest.raises(IntegrityError) as caught:
        await service.rename_for_owner(uuid4(), uuid4(), "Title")

    assert caught.value is error
    assert events == ["execute", "rollback"]
    session.rollback.assert_awaited_once_with()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_commit_failure_rolls_back_and_preserves_original_exception():
    events: list[str] = []
    session = _service_session()
    error = RuntimeError("commit failed")

    async def flush():
        events.append("flush")

    async def commit():
        events.append("commit")
        raise error

    async def rollback():
        events.append("rollback")

    session.add.side_effect = lambda _conversation: events.append("add")
    session.flush.side_effect = flush
    session.commit.side_effect = commit
    session.rollback.side_effect = rollback
    service = ConversationService(session)

    with pytest.raises(RuntimeError) as caught:
        await service.create(uuid4(), "Title")

    assert caught.value is error
    assert events == ["add", "flush", "commit", "rollback"]
    session.commit.assert_awaited_once_with()
    session.rollback.assert_awaited_once_with()
