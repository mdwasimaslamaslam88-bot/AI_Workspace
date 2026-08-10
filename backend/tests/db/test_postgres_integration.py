import asyncio
import re
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.models import Conversation, Message, MessageRole, User
from app.repositories.conversation import ConversationPagination
from app.repositories.message import MessagePagination
from app.repositories.user import UserRepository
from app.services.conversation import ConversationService
from app.services.message import MessageService
from app.services.user import UserService


pytestmark = pytest.mark.integration


async def _schema_snapshot(engine: AsyncEngine) -> dict:
    async with engine.connect() as connection:
        return await connection.run_sync(_inspect_schema)


def _inspect_schema(connection) -> dict:
    inspector = sa.inspect(connection)
    return {
        "tables": set(inspector.get_table_names()),
        "conversation_foreign_keys": inspector.get_foreign_keys("conversations"),
        "message_foreign_keys": inspector.get_foreign_keys("messages"),
        "conversation_indexes": inspector.get_indexes("conversations"),
        "conversation_checks": inspector.get_check_constraints("conversations"),
        "message_checks": inspector.get_check_constraints("messages"),
        "message_uniques": inspector.get_unique_constraints("messages"),
        "columns": {
            table_name: inspector.get_columns(table_name)
            for table_name in ("users", "conversations", "messages")
        },
    }


def _foreign_key(snapshot: dict, table: str, name: str) -> dict:
    foreign_keys = snapshot[f"{table}_foreign_keys"]
    return next(item for item in foreign_keys if item["name"] == name)


def _checks_by_name(items: list[dict]) -> dict[str, str]:
    return {
        item["name"]: " ".join(item["sqltext"].lower().split())
        for item in items
    }


def _columns_by_name(snapshot: dict, table_name: str) -> dict[str, dict]:
    return {
        column["name"]: column
        for column in snapshot["columns"][table_name]
    }


def _assert_required_uuid(column: dict) -> None:
    assert isinstance(column["type"], PostgreSQLUUID)
    assert column["nullable"] is False


def _assert_required_timestamp(column: dict) -> None:
    assert isinstance(column["type"], sa.DateTime)
    assert column["type"].timezone is True
    assert column["nullable"] is False
    assert column["default"] is not None
    assert "now()" in column["default"].lower()


@pytest.mark.asyncio
async def test_migration_creates_exact_expected_postgresql_schema(
    test_database_engine: AsyncEngine,
):
    snapshot = await _schema_snapshot(test_database_engine)

    assert snapshot["tables"] == {
        "alembic_version",
        "users",
        "conversations",
        "messages",
    }

    owner_fk = _foreign_key(
        snapshot,
        "conversation",
        "fk_conversations_owner_id_users",
    )
    assert owner_fk["constrained_columns"] == ["owner_id"]
    assert owner_fk["referred_table"] == "users"
    assert owner_fk["referred_columns"] == ["id"]
    assert owner_fk["options"]["ondelete"] == "RESTRICT"

    message_fk = _foreign_key(
        snapshot,
        "message",
        "fk_messages_conversation_id_conversations",
    )
    assert message_fk["constrained_columns"] == ["conversation_id"]
    assert message_fk["referred_table"] == "conversations"
    assert message_fk["referred_columns"] == ["id"]
    assert message_fk["options"]["ondelete"] == "CASCADE"

    index = next(
        item
        for item in snapshot["conversation_indexes"]
        if item["name"] == "ix_conversations_owner_updated_at_id"
    )
    assert index["column_names"] == ["owner_id", "updated_at", "id"]
    sorting = index.get("column_sorting", {})
    assert "desc" not in sorting.get("owner_id", ())
    assert "desc" in sorting["updated_at"]
    assert "desc" in sorting["id"]

    conversation_checks = _checks_by_name(snapshot["conversation_checks"])
    assert set(conversation_checks) == {
        "ck_conversations_title_non_blank",
        "ck_conversations_next_message_sequence_positive",
    }
    next_sequence_check = conversation_checks[
        "ck_conversations_next_message_sequence_positive"
    ]
    assert "next_message_sequence" in next_sequence_check
    assert ">= 1" in next_sequence_check
    title_check = conversation_checks["ck_conversations_title_non_blank"]
    assert "title is null" in title_check
    assert "char_length" in title_check
    assert "trim(" in title_check or "btrim(" in title_check
    assert "> 0" in title_check

    message_checks = _checks_by_name(snapshot["message_checks"])
    assert set(message_checks) == {
        "ck_messages_role_allowed",
        "ck_messages_sequence_number_positive",
    }
    role_check = message_checks["ck_messages_role_allowed"]
    assert "role" in role_check
    assert " in " in f" {role_check} " or " any " in f" {role_check} "
    role_values = set(re.findall(r"'([^']+)'", role_check))
    assert role_values == {"system", "user", "assistant", "tool"}
    sequence_check = message_checks["ck_messages_sequence_number_positive"]
    assert "sequence_number" in sequence_check
    assert ">= 1" in sequence_check

    message_unique = next(
        item
        for item in snapshot["message_uniques"]
        if item["name"] == "uq_messages_conversation_sequence_number"
    )
    assert message_unique["column_names"] == ["conversation_id", "sequence_number"]
    assert len(snapshot["message_uniques"]) == 1

    users = _columns_by_name(snapshot, "users")
    assert set(users) == {"id", "created_at", "updated_at"}
    _assert_required_uuid(users["id"])
    _assert_required_timestamp(users["created_at"])
    _assert_required_timestamp(users["updated_at"])

    conversations = _columns_by_name(snapshot, "conversations")
    assert set(conversations) == {
        "id",
        "owner_id",
        "title",
        "next_message_sequence",
        "created_at",
        "updated_at",
    }
    _assert_required_uuid(conversations["id"])
    _assert_required_uuid(conversations["owner_id"])
    assert isinstance(conversations["title"]["type"], sa.String)
    assert conversations["title"]["type"].length == 255
    assert conversations["title"]["nullable"] is True
    assert isinstance(conversations["next_message_sequence"]["type"], sa.BigInteger)
    assert conversations["next_message_sequence"]["nullable"] is False
    _assert_required_timestamp(conversations["created_at"])
    _assert_required_timestamp(conversations["updated_at"])

    messages = _columns_by_name(snapshot, "messages")
    assert set(messages) == {
        "id",
        "conversation_id",
        "role",
        "content",
        "sequence_number",
        "created_at",
        "updated_at",
    }
    _assert_required_uuid(messages["id"])
    _assert_required_uuid(messages["conversation_id"])
    assert isinstance(messages["role"]["type"], sa.String)
    assert messages["role"]["type"].length == 32
    assert messages["role"]["nullable"] is False
    assert isinstance(messages["content"]["type"], sa.Text)
    assert messages["content"]["nullable"] is False
    assert isinstance(messages["sequence_number"]["type"], sa.BigInteger)
    assert messages["sequence_number"]["nullable"] is False
    _assert_required_timestamp(messages["created_at"])
    _assert_required_timestamp(messages["updated_at"])


@pytest.mark.asyncio
async def test_service_persistence_and_conversation_delete_cascade(
    test_database_engine: AsyncEngine,
):
    async with AsyncSession(test_database_engine, expire_on_commit=False) as session:
        user = await UserService(session).create(User())
        conversation = await ConversationService(session).create(
            user.id,
            "PostgreSQL integration",
        )
        message = await MessageService(session).append_for_owner(
            user.id,
            conversation.id,
            MessageRole.USER,
            "integration message",
        )

        assert message is not None
        assert message.sequence_number == 1
        message_id = message.id
        conversation_id = conversation.id
        user_id = user.id

        assert await UserService(session).get_by_id(user_id) is not None
        assert (
            await ConversationService(session).get_for_owner(
                user_id,
                conversation_id,
            )
            is not None
        )
        assert await ConversationService(session).delete_for_owner(
            user_id,
            conversation_id,
        )

    async with AsyncSession(test_database_engine) as verification_session:
        assert await verification_session.get(Conversation, conversation_id) is None
        assert await verification_session.get(Message, message_id) is None
        assert await verification_session.get(User, user_id) is not None


@pytest.mark.asyncio
async def test_conversation_owner_foreign_key_is_enforced(
    test_database_engine: AsyncEngine,
):
    async with AsyncSession(test_database_engine) as session:
        with pytest.raises(IntegrityError):
            await ConversationService(session).create(
                uuid4(),
                "orphan conversation",
            )
        assert not session.in_transaction()


@pytest.mark.asyncio
async def test_repository_flush_can_be_rolled_back(
    test_database_engine: AsyncEngine,
):
    user = User()
    user_id = user.id

    async with AsyncSession(test_database_engine) as session:
        await UserRepository(session).create(user)
        user_id = user.id
        await session.rollback()

    async with AsyncSession(test_database_engine) as verification_session:
        assert await verification_session.get(User, user_id) is None


@pytest.mark.asyncio
async def test_conversation_owner_scoped_crud_and_keyset_pagination(
    test_database_engine: AsyncEngine,
):
    async with AsyncSession(test_database_engine, expire_on_commit=False) as session:
        owner = await UserService(session).create(User())
        other_owner = await UserService(session).create(User())
        owner_id = owner.id
        other_owner_id = other_owner.id
        service = ConversationService(session)
        owned = [
            await service.create(owner_id, f"Owned {position}")
            for position in range(3)
        ]
        foreign = await service.create(other_owner_id, "Foreign")
        owned_ids = {conversation.id for conversation in owned}
        foreign_id = foreign.id

        assert await service.get_for_owner(owner_id, foreign_id) is None
        assert (
            await service.rename_for_owner(
                owner_id,
                foreign_id,
                "Must not rename",
            )
            is None
        )
        assert not await service.delete_for_owner(owner_id, foreign_id)

        first_page = await service.list_for_owner(
            owner_id,
            ConversationPagination(limit=2),
        )
        assert len(first_page.items) == 2
        assert first_page.next_cursor is not None
        second_page = await service.list_for_owner(
            owner_id,
            ConversationPagination(limit=2, cursor=first_page.next_cursor),
        )
        assert len(second_page.items) == 1
        assert second_page.next_cursor is None

        listed = first_page.items + second_page.items
        assert {conversation.id for conversation in listed} == owned_ids
        ordering_keys = [
            (conversation.updated_at, conversation.id.int)
            for conversation in listed
        ]
        assert ordering_keys == sorted(ordering_keys, reverse=True)

        target = listed[0]
        target_id = target.id
        previous_updated_at = target.updated_at
        renamed = await service.rename_for_owner(
            owner_id,
            target_id,
            "  Renamed exactly  ",
        )
        assert renamed is not None
        assert renamed.title == "  Renamed exactly  "
        assert renamed.updated_at >= previous_updated_at
        assert await service.get_for_owner(other_owner_id, target_id) is None
        assert await service.delete_for_owner(owner_id, target_id)
        assert await service.get_for_owner(owner_id, target_id) is None
        assert await service.get_for_owner(other_owner_id, foreign_id) is not None


@pytest.mark.asyncio
async def test_message_ordered_pagination_and_owner_isolation(
    test_database_engine: AsyncEngine,
):
    async with AsyncSession(test_database_engine, expire_on_commit=False) as session:
        owner = await UserService(session).create(User())
        other_owner = await UserService(session).create(User())
        owner_id = owner.id
        other_owner_id = other_owner.id
        conversation = await ConversationService(session).create(owner_id, "Owned")
        foreign_conversation = await ConversationService(session).create(
            other_owner_id,
            "Foreign",
        )
        conversation_id = conversation.id
        foreign_conversation_id = foreign_conversation.id
        service = MessageService(session)

        for role, content in (
            (MessageRole.SYSTEM, "system"),
            (MessageRole.USER, "question"),
            (MessageRole.ASSISTANT, "answer"),
        ):
            assert (
                await service.append_for_owner(
                    owner_id,
                    conversation_id,
                    role,
                    content,
                )
                is not None
            )

        assert (
            await service.append_for_owner(
                other_owner_id,
                conversation_id,
                MessageRole.USER,
                "must not append",
            )
            is None
        )
        fourth = await service.append_for_owner(
            owner_id,
            conversation_id,
            MessageRole.TOOL,
            "tool",
        )
        assert fourth is not None
        assert fourth.sequence_number == 4

        foreign_message = await service.append_for_owner(
            other_owner_id,
            foreign_conversation_id,
            MessageRole.USER,
            "foreign",
        )
        assert foreign_message is not None

        first_page = await service.list_for_owner(
            owner_id,
            conversation_id,
            MessagePagination(limit=2),
        )
        assert [message.sequence_number for message in first_page.items] == [1, 2]
        assert first_page.next_cursor is not None
        second_page = await service.list_for_owner(
            owner_id,
            conversation_id,
            MessagePagination(limit=2, cursor=first_page.next_cursor),
        )
        messages = first_page.items + second_page.items
        assert [message.sequence_number for message in messages] == [1, 2, 3, 4]
        assert [message.content for message in messages] == [
            "system",
            "question",
            "answer",
            "tool",
        ]
        assert second_page.next_cursor is None

        wrong_owner_page = await service.list_for_owner(
            other_owner_id,
            conversation_id,
        )
        assert wrong_owner_page.items == ()
        assert wrong_owner_page.next_cursor is None
        foreign_conversation_page = await service.list_for_owner(
            owner_id,
            foreign_conversation_id,
        )
        assert foreign_conversation_page.items == ()
        assert foreign_conversation_page.next_cursor is None


@pytest.mark.asyncio
async def test_concurrent_message_appends_allocate_unique_contiguous_sequences(
    test_database_engine: AsyncEngine,
):
    async with AsyncSession(test_database_engine, expire_on_commit=False) as session:
        owner = await UserService(session).create(User())
        conversation = await ConversationService(session).create(
            owner.id,
            "Concurrent appends",
        )
        owner_id = owner.id
        conversation_id = conversation.id

    async def append_message(position: int) -> int:
        async with AsyncSession(
            test_database_engine,
            expire_on_commit=False,
        ) as concurrent_session:
            message = await MessageService(concurrent_session).append_for_owner(
                owner_id,
                conversation_id,
                MessageRole.USER,
                f"concurrent {position}",
            )
            assert message is not None
            return message.sequence_number

    append_count = 6
    allocated = await asyncio.gather(
        *(append_message(position) for position in range(append_count))
    )
    assert sorted(allocated) == list(range(1, append_count + 1))

    async with AsyncSession(test_database_engine) as verification_session:
        page = await MessageService(verification_session).list_for_owner(
            owner_id,
            conversation_id,
            MessagePagination(limit=append_count),
        )
        assert [message.sequence_number for message in page.items] == list(
            range(1, append_count + 1)
        )
        assert page.next_cursor is None
