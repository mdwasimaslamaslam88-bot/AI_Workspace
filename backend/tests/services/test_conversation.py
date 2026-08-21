from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Message, MessageRole
from app.models.message import (
    MAX_MESSAGE_CONTENT_CHARACTERS,
    MessageContentTooLargeError,
)
from app.repositories.conversation import ConversationPagination
from app.services.conversation import ConversationService
from app.services.message import MessageService


def _service_session(*, scalar=None, rows=()):
    session = AsyncMock(spec=AsyncSession)
    result = Mock()
    result.scalar_one_or_none.return_value = scalar
    result.scalars.return_value.all.return_value = list(rows)
    session.execute.return_value = result
    return session


def _observe_orchestration(
    session,
    events: list[str],
    *,
    failure_stage: str | None = None,
    error: BaseException | None = None,
):
    added: list[Conversation | Message] = []
    flush_count = 0
    result = session.execute.return_value

    def add(entity):
        added.append(entity)
        if isinstance(entity, Conversation):
            events.append("conversation_add")
        else:
            events.append("message_add")

    async def flush():
        nonlocal flush_count
        flush_count += 1
        stage = "conversation_flush" if flush_count == 1 else "message_flush"
        events.append(stage)
        if failure_stage == stage:
            raise error
        if flush_count == 1 and added[0].id is None:
            added[0].id = uuid4()

    async def execute(_statement):
        events.append("message_sequence_allocation")
        if failure_stage == "message_sequence_allocation":
            raise error
        return result

    async def commit():
        events.append("commit")
        if failure_stage == "commit":
            raise error

    async def rollback():
        events.append("rollback")

    session.add.side_effect = add
    session.flush.side_effect = flush
    session.execute.side_effect = execute
    session.commit.side_effect = commit
    session.rollback.side_effect = rollback
    return added


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
async def test_create_with_initial_message_uses_one_session_and_one_commit(
    monkeypatch,
):
    events: list[str] = []
    session = _service_session(scalar=1)
    added = _observe_orchestration(session, events)
    nested_append = AsyncMock()
    monkeypatch.setattr(MessageService, "append_for_owner", nested_append)
    service = ConversationService(session)
    owner_id = uuid4()

    created = await service.create_with_initial_message_for_owner(
        owner_id,
        "Initial conversation",
        MessageRole.USER,
        "  unchanged initial content  ",
    )

    assert created is not None
    conversation, message = created
    assert added == [conversation, message]
    assert conversation.owner_id == owner_id
    assert conversation.title == "Initial conversation"
    assert message.conversation_id == conversation.id
    assert message.role is MessageRole.USER
    assert message.content == "  unchanged initial content  "
    assert message.sequence_number == 1
    assert service.repository.session is session
    assert service.message_repository.session is session
    assert events == [
        "conversation_add",
        "conversation_flush",
        "message_sequence_allocation",
        "message_add",
        "message_flush",
        "commit",
    ]
    nested_append.assert_not_awaited()
    session.execute.assert_awaited_once()
    assert session.flush.await_count == 2
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_with_system_prompt_assigns_roles_and_commits_once(
    monkeypatch,
):
    events: list[str] = []
    session = _service_session()
    result = session.execute.return_value
    result.scalar_one_or_none.side_effect = [1, 2]
    added = _observe_orchestration(session, events)
    nested_append = AsyncMock()
    monkeypatch.setattr(MessageService, "append_for_owner", nested_append)
    service = ConversationService(session)
    owner_id = uuid4()

    created = await service.create_with_initial_message_for_owner(
        owner_id,
        "System conversation",
        MessageRole.USER,
        "  exact initial content  ",
        system_prompt="  exact system prompt  ",
    )

    assert created is not None
    conversation, initial_message = created
    assert len(added) == 3
    assert added[0] is conversation
    assert added[2] is initial_message
    system_message = added[1]
    assert isinstance(system_message, Message)
    assert system_message.conversation_id == conversation.id
    assert system_message.role is MessageRole.SYSTEM
    assert system_message.content == "  exact system prompt  "
    assert system_message.sequence_number == 1
    assert initial_message.conversation_id == conversation.id
    assert initial_message.role is MessageRole.USER
    assert initial_message.content == "  exact initial content  "
    assert initial_message.sequence_number == 2
    assert service.repository.session is session
    assert service.message_repository.session is session
    assert events == [
        "conversation_add",
        "conversation_flush",
        "message_sequence_allocation",
        "message_add",
        "message_flush",
        "message_sequence_allocation",
        "message_add",
        "message_flush",
        "commit",
    ]
    nested_append.assert_not_awaited()
    assert session.execute.await_count == 2
    assert session.flush.await_count == 3
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_with_system_prompt_accepts_exact_character_boundary():
    events: list[str] = []
    session = _service_session()
    session.execute.return_value.scalar_one_or_none.side_effect = [1, 2]
    added = _observe_orchestration(session, events)
    system_prompt = "é" * MAX_MESSAGE_CONTENT_CHARACTERS

    created = await ConversationService(
        session
    ).create_with_initial_message_for_owner(
        uuid4(),
        "Boundary",
        MessageRole.USER,
        "initial",
        system_prompt=system_prompt,
    )

    assert created is not None
    assert added[1].role is MessageRole.SYSTEM
    assert added[1].content == system_prompt
    assert len(added[1].content) == MAX_MESSAGE_CONTENT_CHARACTERS
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("oversized_field", ["system_prompt", "initial_message"])
async def test_create_with_initial_messages_rejects_before_atomic_work(
    oversized_field,
):
    events: list[str] = []
    session = _service_session(scalar=1)
    _observe_orchestration(session, events)
    oversized = "x" * (MAX_MESSAGE_CONTENT_CHARACTERS + 1)
    content = oversized if oversized_field == "initial_message" else "initial"
    system_prompt = oversized if oversized_field == "system_prompt" else None

    with pytest.raises(MessageContentTooLargeError) as captured:
        await ConversationService(
            session
        ).create_with_initial_message_for_owner(
            uuid4(),
            "Rejected",
            MessageRole.USER,
            content,
            system_prompt=system_prompt,
        )

    assert str(captured.value) == "persisted text is too large"
    assert events == []
    session.execute.assert_not_awaited()
    session.add.assert_not_called()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_stage", "expected_before_rollback"),
    [
        (
            "conversation_flush",
            ["conversation_add", "conversation_flush"],
        ),
        (
            "message_sequence_allocation",
            [
                "conversation_add",
                "conversation_flush",
                "message_sequence_allocation",
            ],
        ),
        (
            "message_flush",
            [
                "conversation_add",
                "conversation_flush",
                "message_sequence_allocation",
                "message_add",
                "message_flush",
            ],
        ),
        (
            "commit",
            [
                "conversation_add",
                "conversation_flush",
                "message_sequence_allocation",
                "message_add",
                "message_flush",
                "commit",
            ],
        ),
    ],
)
async def test_create_with_initial_message_failure_rolls_back_original_exception(
    failure_stage,
    expected_before_rollback,
):
    events: list[str] = []
    session = _service_session(scalar=1)
    error = RuntimeError(f"{failure_stage} failed")
    _observe_orchestration(
        session,
        events,
        failure_stage=failure_stage,
        error=error,
    )
    service = ConversationService(session)

    with pytest.raises(RuntimeError) as caught:
        await service.create_with_initial_message_for_owner(
            uuid4(),
            "Initial conversation",
            MessageRole.USER,
            "initial content",
        )

    assert caught.value is error
    assert events == [*expected_before_rollback, "rollback"]
    session.rollback.assert_awaited_once_with()
    if failure_stage == "commit":
        session.commit.assert_awaited_once_with()
    else:
        session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_with_initial_message_allocation_miss_rolls_back_without_commit():
    events: list[str] = []
    session = _service_session(scalar=None)
    _observe_orchestration(session, events)
    service = ConversationService(session)

    created = await service.create_with_initial_message_for_owner(
        uuid4(),
        None,
        MessageRole.SYSTEM,
        "initial content",
    )

    assert created is None
    assert events == [
        "conversation_add",
        "conversation_flush",
        "message_sequence_allocation",
        "rollback",
    ]
    session.rollback.assert_awaited_once_with()
    session.commit.assert_not_awaited()
    assert session.add.call_count == 1
    session.flush.assert_awaited_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("allocated_sequences", "expected_events", "expected_adds"),
    [
        (
            [None],
            [
                "conversation_add",
                "conversation_flush",
                "message_sequence_allocation",
                "rollback",
            ],
            1,
        ),
        (
            [1, None],
            [
                "conversation_add",
                "conversation_flush",
                "message_sequence_allocation",
                "message_add",
                "message_flush",
                "message_sequence_allocation",
                "rollback",
            ],
            2,
        ),
    ],
)
async def test_create_with_system_prompt_allocation_miss_rolls_back_everything(
    allocated_sequences,
    expected_events,
    expected_adds,
):
    events: list[str] = []
    session = _service_session()
    session.execute.return_value.scalar_one_or_none.side_effect = allocated_sequences
    _observe_orchestration(session, events)
    service = ConversationService(session)

    created = await service.create_with_initial_message_for_owner(
        uuid4(),
        None,
        MessageRole.USER,
        "initial content",
        system_prompt="system content",
    )

    assert created is None
    assert events == expected_events
    assert session.add.call_count == expected_adds
    session.rollback.assert_awaited_once_with()
    session.commit.assert_not_awaited()


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
    assert events == ["execute", "execute", "commit"]
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


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["get", "list"])
async def test_read_failure_rolls_back_and_preserves_original_exception(operation):
    events: list[str] = []
    session = _service_session()
    error = RuntimeError(f"{operation} failed")

    async def execute(_statement):
        events.append("execute")
        raise error

    async def rollback():
        events.append("rollback")

    session.execute.side_effect = execute
    session.rollback.side_effect = rollback
    service = ConversationService(session)

    with pytest.raises(RuntimeError) as caught:
        if operation == "get":
            await service.get_for_owner(uuid4(), uuid4())
        else:
            await service.list_for_owner(uuid4())

    assert caught.value is error
    assert events == ["execute", "rollback"]
    session.rollback.assert_awaited_once_with()
    session.commit.assert_not_awaited()
