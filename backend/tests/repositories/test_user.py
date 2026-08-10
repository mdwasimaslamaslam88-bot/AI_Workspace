from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import Select

from app.models import User
from app.repositories.user import UserRepository


def _session_with_scalar(scalar):
    session = AsyncMock(spec=AsyncSession)
    result = Mock()
    result.scalar_one_or_none.return_value = scalar
    session.execute.return_value = result
    return session


@pytest.mark.asyncio
async def test_create_adds_and_flushes_supplied_user_without_owning_transaction():
    session = _session_with_scalar(None)
    repository = UserRepository(session)
    user = User()

    created = await repository.create(user)

    assert created is user
    session.add.assert_called_once_with(user)
    session.flush.assert_awaited_once_with()
    session.execute.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_by_id_uses_bound_primary_key_predicate_and_returns_user():
    user_id = uuid4()
    user = User(id=user_id)
    session = _session_with_scalar(user)
    repository = UserRepository(session)

    found = await repository.get_by_id(user_id)

    assert found is user
    statement = session.execute.await_args.args[0]
    assert isinstance(statement, Select)
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = " ".join(str(compiled).split()).lower()
    assert "from users" in sql
    assert "where users.id =" in sql
    assert "join" not in sql
    assert "conversations" not in sql
    assert user_id in compiled.params.values()
    session.add.assert_not_called()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_by_id_returns_none_without_repository_transaction_actions():
    session = _session_with_scalar(None)
    repository = UserRepository(session)

    found = await repository.get_by_id(uuid4())

    assert found is None
    session.execute.assert_awaited_once()
    session.add.assert_not_called()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()
