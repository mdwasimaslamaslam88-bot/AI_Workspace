from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import Update
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


@pytest.mark.asyncio
@pytest.mark.parametrize("present", [True, False])
async def test_get_by_access_token_digest_uses_bound_predicate_without_transaction(
    present,
):
    access_token_digest = "a" * 64
    user = User(access_token_digest=access_token_digest) if present else None
    session = _session_with_scalar(user)
    repository = UserRepository(session)

    found = await repository.get_by_access_token_digest(access_token_digest)

    assert found is user
    statement = session.execute.await_args.args[0]
    assert isinstance(statement, Select)
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = " ".join(str(compiled).split()).lower()
    assert "from users" in sql
    assert "where users.access_token_digest =" in sql
    assert "join" not in sql
    assert access_token_digest in compiled.params.values()
    session.add.assert_not_called()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("matched", "expected_result"),
    [(True, True), (False, False)],
)
async def test_rotate_access_token_digest_uses_one_atomic_conditional_update(
    matched,
    expected_result,
):
    user_id = uuid4()
    expected_digest = "a" * 64
    replacement_digest = "b" * 64
    session = _session_with_scalar(user_id if matched else None)
    repository = UserRepository(session)

    rotated = await repository.rotate_access_token_digest(
        user_id,
        expected_digest,
        replacement_digest,
    )

    assert rotated is expected_result
    session.execute.assert_awaited_once()
    statement = session.execute.await_args.args[0]
    assert isinstance(statement, Update)
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = " ".join(str(compiled).split()).lower()
    assert sql.startswith("update users set")
    assert "access_token_digest=" in sql
    assert "where users.id =" in sql
    assert "and users.access_token_digest =" in sql
    assert "returning users.id" in sql
    assert "select" not in sql
    assert user_id in compiled.params.values()
    assert expected_digest in compiled.params.values()
    assert replacement_digest in compiled.params.values()
    session.add.assert_not_called()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()
