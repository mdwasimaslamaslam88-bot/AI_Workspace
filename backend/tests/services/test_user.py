from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.services.user import UserService


def _service_session(scalar=None):
    session = AsyncMock(spec=AsyncSession)
    result = Mock()
    result.scalar_one_or_none.return_value = scalar
    session.execute.return_value = result
    return session


@pytest.mark.asyncio
async def test_successful_create_orders_add_flush_commit_exactly_once():
    events: list[str] = []
    session = _service_session()

    async def flush():
        events.append("flush")

    async def commit():
        events.append("commit")

    session.add.side_effect = lambda _user: events.append("add")
    session.flush.side_effect = flush
    session.commit.side_effect = commit
    service = UserService(session)
    user = User()

    created = await service.create(user)

    assert created is user
    assert events == ["add", "flush", "commit"]
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("present", [True, False])
async def test_get_by_id_never_commits(present):
    user_id = uuid4()
    user = User(id=user_id) if present else None
    session = _service_session(user)
    service = UserService(session)

    found = await service.get_by_id(user_id)

    assert found is user
    session.execute.assert_awaited_once()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


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

    session.add.side_effect = lambda _user: events.append("add")
    session.flush.side_effect = flush
    session.rollback.side_effect = rollback
    service = UserService(session)

    with pytest.raises(RuntimeError) as caught:
        await service.create(User())

    assert caught.value is error
    assert events == ["add", "flush", "rollback"]
    session.rollback.assert_awaited_once_with()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_failure_rolls_back_without_translating_integrity_error():
    events: list[str] = []
    session = _service_session()
    error = IntegrityError("get user", {}, RuntimeError("database constraint"))

    async def execute(_statement):
        events.append("execute")
        raise error

    async def rollback():
        events.append("rollback")

    session.execute.side_effect = execute
    session.rollback.side_effect = rollback
    service = UserService(session)

    with pytest.raises(IntegrityError) as caught:
        await service.get_by_id(uuid4())

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

    session.add.side_effect = lambda _user: events.append("add")
    session.flush.side_effect = flush
    session.commit.side_effect = commit
    session.rollback.side_effect = rollback
    service = UserService(session)

    with pytest.raises(RuntimeError) as caught:
        await service.create(User())

    assert caught.value is error
    assert events == ["add", "flush", "commit", "rollback"]
    session.commit.assert_awaited_once_with()
    session.rollback.assert_awaited_once_with()
