from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import Update
from sqlalchemy.sql.selectable import Select

from app.models import User, UserSession
from app.repositories.user import UserRepository


def _session_with_scalar(scalar, *, row=None):
    session = AsyncMock(spec=AsyncSession)
    result = Mock()
    result.scalar_one_or_none.return_value = scalar
    result.one_or_none.return_value = row
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
    user = User() if present else None
    access_session_id = uuid4()
    session = _session_with_scalar(
        None,
        row=(user, access_session_id) if user is not None else None,
    )
    repository = UserRepository(session)

    found = await repository.get_by_access_token_digest(access_token_digest)

    assert found is user
    statement = session.execute.await_args.args[0]
    assert isinstance(statement, Select)
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = " ".join(str(compiled).split()).lower()
    assert "from users" in sql
    assert "join user_sessions" in sql
    assert "user_sessions.access_token_digest =" in sql
    assert "user_sessions.revoked_at is null" in sql
    assert access_token_digest in compiled.params.values()
    if user is not None:
        assert user.authenticated_session_id == access_session_id
        assert user.authenticated_session_digest == access_token_digest
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
    access_session_id = uuid4()
    expected_digest = "a" * 64
    replacement_digest = "b" * 64
    session = _session_with_scalar(user_id if matched else None)
    repository = UserRepository(session)

    rotated = await repository.rotate_access_token_digest(
        user_id,
        access_session_id,
        expected_digest,
        replacement_digest,
    )

    assert rotated is expected_result
    session.execute.assert_awaited_once()
    statement = session.execute.await_args.args[0]
    assert isinstance(statement, Update)
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = " ".join(str(compiled).split()).lower()
    assert sql.startswith("update user_sessions set")
    assert "access_token_digest=" in sql
    assert "where user_sessions.id =" in sql
    assert "and user_sessions.user_id =" in sql
    assert "and user_sessions.access_token_digest =" in sql
    assert "and user_sessions.revoked_at is null" in sql
    assert "returning user_sessions.id" in sql
    assert "select" not in sql
    assert user_id in compiled.params.values()
    assert access_session_id in compiled.params.values()
    assert expected_digest in compiled.params.values()
    assert replacement_digest in compiled.params.values()
    session.add.assert_not_called()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_access_session_adds_and_flushes_without_commit():
    session = _session_with_scalar(None)
    access_session = UserSession(
        id=uuid4(),
        user_id=uuid4(),
        access_token_digest="c" * 64,
        label="Linux desktop",
    )

    created = await UserRepository(session).create_access_session(access_session)

    assert created is access_session
    session.add.assert_called_once_with(access_session)
    session.flush.assert_awaited_once_with()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(("owner_exists", "active_count"), [(True, 3), (False, None)])
async def test_lock_owner_and_count_active_sessions_is_serialized(
    owner_exists,
    active_count,
):
    user_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    owner_result = Mock()
    owner_result.scalar_one_or_none.return_value = user_id if owner_exists else None
    count_result = Mock()
    count_result.scalar_one.return_value = 3
    session.execute.side_effect = [owner_result, count_result]

    returned = await UserRepository(session).lock_owner_and_count_active_sessions(
        user_id
    )

    assert returned == active_count
    first_sql = " ".join(
        str(session.execute.await_args_list[0].args[0]).lower().split()
    )
    assert "from users" in first_sql
    assert "users.id" in first_sql
    assert "for update" in first_sql
    assert session.execute.await_count == (2 if owner_exists else 1)


@pytest.mark.asyncio
async def test_list_active_sessions_filters_owner_and_revocation():
    user_id = uuid4()
    access_session = UserSession(
        id=uuid4(),
        user_id=user_id,
        access_token_digest="d" * 64,
    )
    session = AsyncMock(spec=AsyncSession)
    result = Mock()
    result.scalars.return_value.all.return_value = [access_session]
    session.execute.return_value = result

    returned = await UserRepository(session).list_active_sessions_for_owner(user_id)

    assert returned == (access_session,)
    sql = " ".join(str(session.execute.await_args.args[0]).lower().split())
    assert "user_sessions.user_id" in sql
    assert "user_sessions.revoked_at is null" in sql


@pytest.mark.asyncio
async def test_revoke_active_session_is_owner_scoped_and_idempotent():
    user_id = uuid4()
    access_session_id = uuid4()
    session = _session_with_scalar(access_session_id)

    revoked = await UserRepository(session).revoke_active_session_for_owner(
        user_id,
        access_session_id,
    )

    assert revoked is True
    sql = " ".join(str(session.execute.await_args.args[0]).lower().split())
    assert sql.startswith("update user_sessions set")
    assert "user_sessions.id" in sql
    assert "user_sessions.user_id" in sql
    assert "user_sessions.revoked_at is null" in sql
