from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.services.user as user_service_module
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


@pytest.mark.asyncio
async def test_provision_persists_only_digest_and_commits_exactly_once(monkeypatch):
    events: list[str] = []
    session = _service_session()
    access_token = "A" * 43
    access_token_digest = "b" * 64
    monkeypatch.setattr(
        user_service_module,
        "generate_access_token",
        Mock(return_value=access_token),
    )
    digest = Mock(return_value=access_token_digest)
    monkeypatch.setattr(user_service_module, "digest_access_token", digest)

    session.add.side_effect = lambda _user: events.append("add")

    async def flush():
        events.append("flush")

    async def commit():
        events.append("commit")

    session.flush.side_effect = flush
    session.commit.side_effect = commit
    service = UserService(session)

    user, returned_token = await service.provision_with_access_token()

    assert returned_token == access_token
    assert user.access_token_digest == access_token_digest
    assert access_token not in user.__dict__.values()
    digest.assert_called_once_with(access_token)
    session.add.assert_called_once_with(user)
    assert events == ["add", "flush", "commit"]
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["flush", "commit"])
async def test_provision_failure_rolls_back_and_preserves_original_exception(
    monkeypatch,
    failure_stage,
):
    events: list[str] = []
    session = _service_session()
    error = RuntimeError(f"{failure_stage} failed")
    monkeypatch.setattr(
        user_service_module,
        "generate_access_token",
        Mock(return_value="A" * 43),
    )

    session.add.side_effect = lambda _user: events.append("add")

    async def flush():
        events.append("flush")
        if failure_stage == "flush":
            raise error

    async def commit():
        events.append("commit")
        if failure_stage == "commit":
            raise error

    async def rollback():
        events.append("rollback")

    session.flush.side_effect = flush
    session.commit.side_effect = commit
    session.rollback.side_effect = rollback
    service = UserService(session)

    with pytest.raises(RuntimeError) as caught:
        await service.provision_with_access_token()

    assert caught.value is error
    expected = ["add", "flush"]
    if failure_stage == "commit":
        expected.append("commit")
    assert events == [*expected, "rollback"]
    session.rollback.assert_awaited_once_with()
    if failure_stage == "commit":
        session.commit.assert_awaited_once_with()
    else:
        session.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("present", [True, False])
async def test_access_token_digest_lookup_never_commits(present):
    access_token_digest = "c" * 64
    user = User(access_token_digest=access_token_digest) if present else None
    session = _service_session(user)
    service = UserService(session)

    found = await service.get_by_access_token_digest(access_token_digest)

    assert found is user
    session.execute.assert_awaited_once()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_access_token_lookup_failure_rolls_back_original_exception():
    session = _service_session()
    error = IntegrityError(
        "authenticate user",
        {},
        RuntimeError("database failure"),
    )
    session.execute.side_effect = error
    service = UserService(session)

    with pytest.raises(IntegrityError) as caught:
        await service.get_by_access_token_digest("d" * 64)

    assert caught.value is error
    session.rollback.assert_awaited_once_with()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_rotate_access_token_persists_only_replacement_digest_and_commits_once(
    monkeypatch,
):
    events: list[str] = []
    session = _service_session()
    user_id = uuid4()
    expected_digest = "a" * 64
    replacement_token = "B" * 43
    replacement_digest = "c" * 64
    monkeypatch.setattr(
        user_service_module,
        "generate_access_token",
        Mock(return_value=replacement_token),
    )
    digest = Mock(return_value=replacement_digest)
    monkeypatch.setattr(user_service_module, "digest_access_token", digest)
    service = UserService(session)

    async def rotate(*_args):
        events.append("update")
        return True

    replace_digest = AsyncMock(side_effect=rotate)
    service.repository.rotate_access_token_digest = replace_digest

    async def commit():
        events.append("commit")

    session.commit.side_effect = commit

    returned_token = await service.rotate_access_token(
        user_id,
        expected_digest,
    )

    assert returned_token == replacement_token
    digest.assert_called_once_with(replacement_token)
    replace_digest.assert_awaited_once_with(
        user_id,
        expected_digest,
        replacement_digest,
    )
    assert replacement_token not in replace_digest.await_args.args
    assert events == ["update", "commit"]
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_rotate_access_token_compare_and_swap_miss_rolls_back_without_token(
    monkeypatch,
):
    events: list[str] = []
    session = _service_session()
    replacement_token = "B" * 43
    replacement_digest = "c" * 64
    monkeypatch.setattr(
        user_service_module,
        "generate_access_token",
        Mock(return_value=replacement_token),
    )
    monkeypatch.setattr(
        user_service_module,
        "digest_access_token",
        Mock(return_value=replacement_digest),
    )
    service = UserService(session)

    async def rotate(*_args):
        events.append("update")
        return False

    replace_digest = AsyncMock(side_effect=rotate)
    service.repository.rotate_access_token_digest = replace_digest

    async def rollback():
        events.append("rollback")

    session.rollback.side_effect = rollback

    returned_token = await service.rotate_access_token(uuid4(), "a" * 64)

    assert returned_token is None
    assert events == ["update", "rollback"]
    session.rollback.assert_awaited_once_with()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["update", "commit"])
async def test_rotate_access_token_failure_rolls_back_original_exception(
    monkeypatch,
    failure_stage,
):
    events: list[str] = []
    session = _service_session()
    error = IntegrityError(
        "rotate access token",
        {},
        RuntimeError("credential uniqueness failure"),
    )
    monkeypatch.setattr(
        user_service_module,
        "generate_access_token",
        Mock(return_value="B" * 43),
    )
    service = UserService(session)

    async def rotate(*_args):
        events.append("update")
        if failure_stage == "update":
            raise error
        return True

    service.repository.rotate_access_token_digest = AsyncMock(side_effect=rotate)

    async def commit():
        events.append("commit")
        if failure_stage == "commit":
            raise error

    async def rollback():
        events.append("rollback")

    session.commit.side_effect = commit
    session.rollback.side_effect = rollback

    with pytest.raises(IntegrityError) as caught:
        await service.rotate_access_token(uuid4(), "a" * 64)

    assert caught.value is error
    expected_events = ["update"]
    if failure_stage == "commit":
        expected_events.append("commit")
    assert events == [*expected_events, "rollback"]
    session.rollback.assert_awaited_once_with()
    if failure_stage == "commit":
        session.commit.assert_awaited_once_with()
    else:
        session.commit.assert_not_awaited()
