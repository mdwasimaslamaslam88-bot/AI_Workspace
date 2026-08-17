import asyncio
from datetime import datetime, timezone
import re
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient
import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import app.services.conversation_generation as generation_module
from app.ai.catalog import (
    ModelAvailability,
    ModelCatalog,
    RuntimeModel,
)
from app.ai.generation import (
    TextGenerationResult,
    TextGenerationRouter,
    TextGenerationRuntimeUnavailableError,
)
from app.core.security import digest_access_token, generate_access_token
from app.main import app
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
        "user_uniques": inspector.get_unique_constraints("users"),
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
    assert set(users) == {
        "id",
        "access_token_digest",
        "created_at",
        "updated_at",
    }
    _assert_required_uuid(users["id"])
    assert isinstance(users["access_token_digest"]["type"], sa.String)
    assert users["access_token_digest"]["type"].length == 64
    assert users["access_token_digest"]["nullable"] is True
    _assert_required_timestamp(users["created_at"])
    _assert_required_timestamp(users["updated_at"])
    user_unique = next(
        item
        for item in snapshot["user_uniques"]
        if item["name"] == "uq_users_access_token_digest"
    )
    assert user_unique["column_names"] == ["access_token_digest"]
    assert len(snapshot["user_uniques"]) == 1

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


@pytest.mark.asyncio
async def test_create_with_system_prompt_persists_one_atomic_conversation(
    test_database_engine: AsyncEngine,
):
    async with AsyncSession(test_database_engine, expire_on_commit=False) as session:
        owner = await UserService(session).create(User())
        owner_id = owner.id

        created = await ConversationService(
            session
        ).create_with_initial_message_for_owner(
            owner_id,
            "  Initial title  ",
            MessageRole.USER,
            "  Initial content  ",
            system_prompt="  Exact system content  ",
        )

        assert created is not None
        conversation, message = created
        conversation_id = conversation.id
        message_id = message.id
        assert conversation.owner_id == owner_id
        assert conversation.title == "  Initial title  "
        assert message.conversation_id == conversation_id
        assert message.role is MessageRole.USER
        assert message.content == "  Initial content  "
        assert message.sequence_number == 2

    async with AsyncSession(test_database_engine) as verification_session:
        conversations = (
            (
                await verification_session.execute(
                    sa.select(Conversation).where(Conversation.owner_id == owner_id)
                )
            )
            .scalars()
            .all()
        )
        messages = (
            (
                await verification_session.execute(
                    sa.select(Message).where(
                        Message.conversation_id == conversation_id
                    ).order_by(Message.sequence_number)
                )
            )
            .scalars()
            .all()
        )

        assert len(conversations) == 1
        assert conversations[0].id == conversation_id
        assert conversations[0].next_message_sequence == 3
        assert len(messages) == 2
        assert messages[0].conversation_id == conversation_id
        assert messages[0].role is MessageRole.SYSTEM
        assert messages[0].content == "  Exact system content  "
        assert messages[0].sequence_number == 1
        assert messages[1].id == message_id
        assert messages[1].conversation_id == conversation_id
        assert messages[1].role is MessageRole.USER
        assert messages[1].content == "  Initial content  "
        assert messages[1].sequence_number == 2


@pytest.mark.asyncio
async def test_create_with_initial_message_failure_rolls_back_all_persistence(
    test_database_engine: AsyncEngine,
):
    async with AsyncSession(test_database_engine, expire_on_commit=False) as session:
        owner = await UserService(session).create(User())
        owner_id = owner.id

        with pytest.raises(IntegrityError):
            await ConversationService(
                session
            ).create_with_initial_message_for_owner(
                owner_id,
                "Must roll back",
                MessageRole.USER,
                None,  # type: ignore[arg-type]
                system_prompt="System content must also roll back",
            )

        assert not session.in_transaction()

    async with AsyncSession(test_database_engine) as verification_session:
        conversations = (
            (
                await verification_session.execute(
                    sa.select(Conversation).where(Conversation.owner_id == owner_id)
                )
            )
            .scalars()
            .all()
        )
        messages = (
            (
                await verification_session.execute(
                    sa.select(Message)
                    .join(
                        Conversation,
                        Conversation.id == Message.conversation_id,
                    )
                    .where(Conversation.owner_id == owner_id)
                )
            )
            .scalars()
            .all()
        )

        assert conversations == []
        assert messages == []
        assert await verification_session.get(User, owner_id) is not None


@pytest.mark.asyncio
async def test_user_access_credential_persistence_lookup_and_uniqueness(
    test_database_engine: AsyncEngine,
):
    async with AsyncSession(
        test_database_engine,
        expire_on_commit=False,
    ) as session:
        service = UserService(session)
        user, access_token = await service.provision_with_access_token()
        user_id = user.id
        expected_digest = digest_access_token(access_token)

        assert user.access_token_digest == expected_digest
        assert user.access_token_digest != access_token
        assert (
            await service.get_by_access_token_digest(expected_digest)
        ).id == user_id
        assert (
            await service.get_by_access_token_digest(
                digest_access_token(generate_access_token())
            )
            is None
        )

    async with AsyncSession(test_database_engine) as verification_session:
        stored = await verification_session.get(User, user_id)
        assert stored is not None
        assert stored.access_token_digest == expected_digest
        assert stored.access_token_digest != access_token

        with pytest.raises(IntegrityError):
            await UserService(verification_session).create(
                User(access_token_digest=expected_digest)
            )

        assert not verification_session.in_transaction()
        original = await UserService(
            verification_session
        ).get_by_access_token_digest(expected_digest)
        assert original is not None
        assert original.id == user_id


@pytest.mark.asyncio
async def test_user_access_credential_flush_can_be_rolled_back(
    test_database_engine: AsyncEngine,
):
    access_token = generate_access_token()
    access_token_digest = digest_access_token(access_token)

    async with AsyncSession(
        test_database_engine,
        expire_on_commit=False,
    ) as session:
        user = await UserRepository(session).create(
            User(access_token_digest=access_token_digest)
        )
        user_id = user.id
        await session.rollback()

    async with AsyncSession(test_database_engine) as verification_session:
        assert await verification_session.get(User, user_id) is None
        assert (
            await UserService(
                verification_session
            ).get_by_access_token_digest(access_token_digest)
            is None
        )


@pytest.mark.asyncio
async def test_authenticated_conversation_creation_uses_current_user_and_sequence(
    test_database_engine: AsyncEngine,
):
    session_factory = async_sessionmaker(
        test_database_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    missing = object()
    previous_factory = getattr(app.state, "db_session_factory", missing)
    app.state.db_session_factory = session_factory

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            provisioned = await client.post("/api/v1/users")
            assert provisioned.status_code == 201
            user_payload = provisioned.json()
            user_id = UUID(user_payload["id"])
            access_token = user_payload["access_token"]

            created = await client.post(
                "/api/v1/conversations",
                headers={"Authorization": f"Bearer {access_token}"},
                json={
                    "title": "  API integration  ",
                    "initial_message": "  Exact API content  ",
                },
            )
            assert created.status_code == 201
            created_payload = created.json()

            appended = await client.post(
                f"/api/v1/conversations/{created_payload['id']}/messages",
                headers={"Authorization": f"Bearer {access_token}"},
                json={"content": "  Exact API follow-up  "},
            )
            assert appended.status_code == 201
            appended_payload = appended.json()

            for invalid_content in ("", " \t\r\n"):
                rejected_creation = await client.post(
                    "/api/v1/conversations",
                    headers={"Authorization": f"Bearer {access_token}"},
                    json={"initial_message": invalid_content},
                )
                assert rejected_creation.status_code == 422
                assert (
                    rejected_creation.json()["error"]["code"]
                    == "VALIDATION_ERROR"
                )

                rejected_append = await client.post(
                    f"/api/v1/conversations/{created_payload['id']}/messages",
                    headers={"Authorization": f"Bearer {access_token}"},
                    json={"content": invalid_content},
                )
                assert rejected_append.status_code == 422
                assert (
                    rejected_append.json()["error"]["code"]
                    == "VALIDATION_ERROR"
                )

            additional_owned_payloads = []
            for position in range(2):
                additional_owned = await client.post(
                    "/api/v1/conversations",
                    headers={"Authorization": f"Bearer {access_token}"},
                    json={
                        "title": f"Owned API conversation {position}",
                        "initial_message": f"Owned initial message {position}",
                    },
                )
                assert additional_owned.status_code == 201
                additional_owned_payloads.append(additional_owned.json())

            first_message_page = await client.get(
                f"/api/v1/conversations/{created_payload['id']}/messages",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"limit": 1},
            )
            assert first_message_page.status_code == 200
            first_message_page_payload = first_message_page.json()

            second_message_page = await client.get(
                f"/api/v1/conversations/{created_payload['id']}/messages",
                headers={"Authorization": f"Bearer {access_token}"},
                params={
                    "limit": 1,
                    "cursor": first_message_page_payload["next_cursor"],
                },
            )
            assert second_message_page.status_code == 200
            second_message_page_payload = second_message_page.json()

            async with AsyncSession(
                test_database_engine,
                expire_on_commit=False,
            ) as setup_session:
                empty_conversation = await ConversationService(
                    setup_session
                ).create(user_id, "Empty API conversation")

            empty_message_page = await client.get(
                f"/api/v1/conversations/{empty_conversation.id}/messages",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            assert empty_message_page.status_code == 200
            empty_message_page_payload = empty_message_page.json()

            missing_message_page = await client.get(
                f"/api/v1/conversations/{uuid4()}/messages",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            assert missing_message_page.status_code == 200
            missing_message_page_payload = missing_message_page.json()

            second_provisioned = await client.post("/api/v1/users")
            assert second_provisioned.status_code == 201
            second_user_payload = second_provisioned.json()
            foreign_conversation = await client.post(
                "/api/v1/conversations",
                headers={
                    "Authorization": (
                        f"Bearer {second_user_payload['access_token']}"
                    )
                },
                json={
                    "title": "Foreign API conversation",
                    "initial_message": "Foreign initial message",
                },
            )
            assert foreign_conversation.status_code == 201
            foreign_conversation_payload = foreign_conversation.json()

            empty_user = await client.post("/api/v1/users")
            assert empty_user.status_code == 201
            empty_user_payload = empty_user.json()
            spoofed_owner = await client.post(
                "/api/v1/conversations",
                headers={
                    "Authorization": (
                        f"Bearer {second_user_payload['access_token']}"
                    )
                },
                json={
                    "owner_id": str(user_id),
                    "title": "Must not persist",
                    "initial_message": "Must not persist",
                },
            )
            assert spoofed_owner.status_code == 422

            foreign_append = await client.post(
                f"/api/v1/conversations/{created_payload['id']}/messages",
                headers={
                    "Authorization": (
                        f"Bearer {second_user_payload['access_token']}"
                    )
                },
                json={"content": "Must not persist"},
            )
            assert foreign_append.status_code == 404
            assert foreign_append.json()["error"] == {
                "code": "HTTP_ERROR",
                "message": "Conversation not found",
            }

            foreign_message_page = await client.get(
                f"/api/v1/conversations/{created_payload['id']}/messages",
                headers={
                    "Authorization": (
                        f"Bearer {second_user_payload['access_token']}"
                    )
                },
            )
            assert foreign_message_page.status_code == 200
            foreign_message_page_payload = foreign_message_page.json()

            conversation_id = UUID(created_payload["id"])
            async with AsyncSession(
                test_database_engine,
                expire_on_commit=False,
            ) as setup_session:
                before_get_conversation = (
                    await setup_session.execute(
                        sa.select(
                            Conversation.owner_id,
                            Conversation.updated_at,
                            Conversation.next_message_sequence,
                        ).where(Conversation.id == conversation_id)
                    )
                ).one()
                before_get_messages = [
                    tuple(row)
                    for row in (
                        await setup_session.execute(
                            sa.select(
                                Message.id,
                                Message.conversation_id,
                                Message.role,
                                Message.content,
                                Message.sequence_number,
                                Message.created_at,
                                Message.updated_at,
                            )
                            .where(Message.conversation_id == conversation_id)
                            .order_by(Message.sequence_number)
                        )
                    ).all()
                ]

            owned_conversation_response = await client.get(
                f"/api/v1/conversations/{conversation_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            assert owned_conversation_response.status_code == 200
            owned_conversation_payload = owned_conversation_response.json()
            assert set(owned_conversation_payload) == {
                "id",
                "title",
                "created_at",
                "updated_at",
            }
            assert owned_conversation_payload["id"] == str(conversation_id)
            assert owned_conversation_payload["title"] == "  API integration  "
            owned_response_text = owned_conversation_response.text.lower()
            assert "owner_id" not in owned_response_text
            assert "next_message_sequence" not in owned_response_text
            assert "messages" not in owned_response_text
            assert "credential" not in owned_response_text
            assert "digest" not in owned_response_text

            foreign_get_response = await client.get(
                f"/api/v1/conversations/{conversation_id}",
                headers={
                    "Authorization": (
                        f"Bearer {second_user_payload['access_token']}"
                    )
                },
            )
            missing_get_response = await client.get(
                f"/api/v1/conversations/{uuid4()}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            expected_not_found = {
                "code": "HTTP_ERROR",
                "message": "Conversation not found",
            }
            assert foreign_get_response.status_code == 404
            assert missing_get_response.status_code == 404
            assert foreign_get_response.json()["error"] == expected_not_found
            assert missing_get_response.json()["error"] == expected_not_found

            async with AsyncSession(
                test_database_engine,
                expire_on_commit=False,
            ) as verification_session:
                after_get_conversation = (
                    await verification_session.execute(
                        sa.select(
                            Conversation.owner_id,
                            Conversation.updated_at,
                            Conversation.next_message_sequence,
                        ).where(Conversation.id == conversation_id)
                    )
                ).one()
                after_get_messages = [
                    tuple(row)
                    for row in (
                        await verification_session.execute(
                            sa.select(
                                Message.id,
                                Message.conversation_id,
                                Message.role,
                                Message.content,
                                Message.sequence_number,
                                Message.created_at,
                                Message.updated_at,
                            )
                            .where(Message.conversation_id == conversation_id)
                            .order_by(Message.sequence_number)
                        )
                    ).all()
                ]

            assert tuple(after_get_conversation) == tuple(before_get_conversation)
            assert after_get_messages == before_get_messages

            rename_title = "  Renamed through API  "
            rename_response = await client.patch(
                f"/api/v1/conversations/{conversation_id}",
                headers={"Authorization": f"Bearer {access_token}"},
                json={"title": rename_title},
            )
            assert rename_response.status_code == 200
            rename_payload = rename_response.json()
            assert set(rename_payload) == {
                "id",
                "title",
                "created_at",
                "updated_at",
            }
            assert rename_payload["id"] == str(conversation_id)
            assert rename_payload["title"] == rename_title
            rename_response_text = rename_response.text.lower()
            assert "owner_id" not in rename_response_text
            assert "next_message_sequence" not in rename_response_text
            assert "messages" not in rename_response_text
            assert "credential" not in rename_response_text
            assert "digest" not in rename_response_text

            renamed_get_response = await client.get(
                f"/api/v1/conversations/{conversation_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            assert renamed_get_response.status_code == 200
            assert renamed_get_response.json()["title"] == rename_title

            renamed_message_history = await client.get(
                f"/api/v1/conversations/{conversation_id}/messages",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            assert renamed_message_history.status_code == 200
            renamed_message_history_payload = renamed_message_history.json()
            assert [
                item["sequence_number"]
                for item in renamed_message_history_payload["items"]
            ] == [1, 2]
            assert renamed_message_history_payload["next_cursor"] is None

            foreign_conversation_id = UUID(foreign_conversation_payload["id"])
            affected_conversation_ids = [
                conversation_id,
                foreign_conversation_id,
            ]
            async with AsyncSession(
                test_database_engine,
                expire_on_commit=False,
            ) as verification_session:
                owner_after_rename = (
                    await verification_session.execute(
                        sa.select(
                            Conversation.owner_id,
                            Conversation.title,
                            Conversation.updated_at,
                            Conversation.next_message_sequence,
                        ).where(Conversation.id == conversation_id)
                    )
                ).one()
                foreign_before_failed_renames = (
                    await verification_session.execute(
                        sa.select(
                            Conversation.owner_id,
                            Conversation.title,
                            Conversation.updated_at,
                            Conversation.next_message_sequence,
                        ).where(Conversation.id == foreign_conversation_id)
                    )
                ).one()
                messages_before_failed_renames = [
                    tuple(row)
                    for row in (
                        await verification_session.execute(
                            sa.select(
                                Message.id,
                                Message.conversation_id,
                                Message.role,
                                Message.content,
                                Message.sequence_number,
                                Message.created_at,
                                Message.updated_at,
                            )
                            .where(
                                Message.conversation_id.in_(
                                    affected_conversation_ids
                                )
                            )
                            .order_by(
                                Message.conversation_id,
                                Message.sequence_number,
                            )
                        )
                    ).all()
                ]

            assert owner_after_rename.owner_id == before_get_conversation.owner_id
            assert owner_after_rename.title == rename_title
            assert (
                owner_after_rename.next_message_sequence
                == before_get_conversation.next_message_sequence
            )
            assert (
                owner_after_rename.updated_at
                >= before_get_conversation.updated_at
            )
            response_updated_at = datetime.fromisoformat(
                rename_payload["updated_at"].replace("Z", "+00:00")
            )
            assert response_updated_at == owner_after_rename.updated_at
            assert [
                row
                for row in messages_before_failed_renames
                if row[1] == conversation_id
            ] == before_get_messages

            foreign_rename_response = await client.patch(
                f"/api/v1/conversations/{foreign_conversation_id}",
                headers={"Authorization": f"Bearer {access_token}"},
                json={"title": "Must not rename foreign conversation"},
            )
            missing_rename_response = await client.patch(
                f"/api/v1/conversations/{uuid4()}",
                headers={"Authorization": f"Bearer {access_token}"},
                json={"title": "Must not rename missing conversation"},
            )
            expected_rename_not_found = {
                "code": "HTTP_ERROR",
                "message": "Conversation not found",
            }
            assert foreign_rename_response.status_code == 404
            assert missing_rename_response.status_code == 404
            assert (
                foreign_rename_response.json()["error"]
                == expected_rename_not_found
            )
            assert (
                missing_rename_response.json()["error"]
                == expected_rename_not_found
            )

            async with AsyncSession(
                test_database_engine,
                expire_on_commit=False,
            ) as verification_session:
                owner_after_failed_renames = (
                    await verification_session.execute(
                        sa.select(
                            Conversation.owner_id,
                            Conversation.title,
                            Conversation.updated_at,
                            Conversation.next_message_sequence,
                        ).where(Conversation.id == conversation_id)
                    )
                ).one()
                foreign_after_failed_renames = (
                    await verification_session.execute(
                        sa.select(
                            Conversation.owner_id,
                            Conversation.title,
                            Conversation.updated_at,
                            Conversation.next_message_sequence,
                        ).where(Conversation.id == foreign_conversation_id)
                    )
                ).one()
                messages_after_failed_renames = [
                    tuple(row)
                    for row in (
                        await verification_session.execute(
                            sa.select(
                                Message.id,
                                Message.conversation_id,
                                Message.role,
                                Message.content,
                                Message.sequence_number,
                                Message.created_at,
                                Message.updated_at,
                            )
                            .where(
                                Message.conversation_id.in_(
                                    affected_conversation_ids
                                )
                            )
                            .order_by(
                                Message.conversation_id,
                                Message.sequence_number,
                            )
                        )
                    ).all()
                ]

            assert tuple(owner_after_failed_renames) == tuple(owner_after_rename)
            assert tuple(foreign_after_failed_renames) == tuple(
                foreign_before_failed_renames
            )
            assert (
                messages_after_failed_renames
                == messages_before_failed_renames
            )

            owned_conversation_ids = [
                UUID(created_payload["id"]),
                *(UUID(payload["id"]) for payload in additional_owned_payloads),
                empty_conversation.id,
            ]
            newer_ids = owned_conversation_ids[:2]
            older_ids = owned_conversation_ids[2:]
            newer_timestamp = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
            older_timestamp = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
            async with AsyncSession(
                test_database_engine,
                expire_on_commit=False,
            ) as setup_session:
                await setup_session.execute(
                    sa.update(Conversation)
                    .where(Conversation.id.in_(newer_ids))
                    .values(updated_at=newer_timestamp)
                )
                await setup_session.execute(
                    sa.update(Conversation)
                    .where(Conversation.id.in_(older_ids))
                    .values(updated_at=older_timestamp)
                )
                await setup_session.commit()
                before_listing_rows = (
                    await setup_session.execute(
                        sa.select(
                            Conversation.id,
                            Conversation.owner_id,
                            Conversation.updated_at,
                            Conversation.next_message_sequence,
                        ).where(Conversation.id.in_(owned_conversation_ids))
                    )
                ).all()
                before_listing = {
                    row.id: (
                        row.owner_id,
                        row.updated_at,
                        row.next_message_sequence,
                    )
                    for row in before_listing_rows
                }

            first_conversation_page = await client.get(
                "/api/v1/conversations",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"limit": 2},
            )
            assert first_conversation_page.status_code == 200
            first_conversation_page_payload = first_conversation_page.json()
            returned_conversation_cursor = first_conversation_page_payload[
                "next_cursor"
            ]
            assert returned_conversation_cursor is not None

            second_conversation_page = await client.get(
                "/api/v1/conversations",
                headers={"Authorization": f"Bearer {access_token}"},
                params={
                    "limit": 2,
                    "cursor_updated_at": returned_conversation_cursor[
                        "updated_at"
                    ],
                    "cursor_id": returned_conversation_cursor["id"],
                },
            )
            assert second_conversation_page.status_code == 200
            second_conversation_page_payload = second_conversation_page.json()

            foreign_conversation_page = await client.get(
                "/api/v1/conversations",
                headers={
                    "Authorization": (
                        f"Bearer {second_user_payload['access_token']}"
                    )
                },
            )
            assert foreign_conversation_page.status_code == 200
            foreign_conversation_page_payload = foreign_conversation_page.json()

            empty_conversation_page = await client.get(
                "/api/v1/conversations",
                headers={
                    "Authorization": f"Bearer {empty_user_payload['access_token']}"
                },
            )
            assert empty_conversation_page.status_code == 200
            empty_conversation_page_payload = empty_conversation_page.json()


            deletion_target = await client.post(
                "/api/v1/conversations",
                headers={"Authorization": f"Bearer {access_token}"},
                json={
                    "title": "Deletion target",
                    "initial_message": "Deletion target initial message",
                },
            )
            assert deletion_target.status_code == 201
            deletion_target_payload = deletion_target.json()
            deletion_target_id = UUID(deletion_target_payload["id"])

            deletion_target_append = await client.post(
                f"/api/v1/conversations/{deletion_target_id}/messages",
                headers={"Authorization": f"Bearer {access_token}"},
                json={"content": "Deletion target follow-up message"},
            )
            assert deletion_target_append.status_code == 201
            deletion_target_append_payload = deletion_target_append.json()

            deletion_snapshot_ids = [
                *owned_conversation_ids,
                foreign_conversation_id,
                deletion_target_id,
            ]

            async def deletion_state():
                async with AsyncSession(
                    test_database_engine,
                    expire_on_commit=False,
                ) as verification_session:
                    owner_user = tuple(
                        (
                            await verification_session.execute(
                                sa.select(
                                    User.id,
                                    User.access_token_digest,
                                    User.created_at,
                                    User.updated_at,
                                ).where(User.id == user_id)
                            )
                        ).one()
                    )
                    conversation_rows = (
                        await verification_session.execute(
                            sa.select(
                                Conversation.id,
                                Conversation.owner_id,
                                Conversation.title,
                                Conversation.next_message_sequence,
                                Conversation.created_at,
                                Conversation.updated_at,
                            )
                            .where(
                                Conversation.id.in_(deletion_snapshot_ids)
                            )
                            .order_by(Conversation.id)
                        )
                    ).all()
                    message_rows = (
                        await verification_session.execute(
                            sa.select(
                                Message.id,
                                Message.conversation_id,
                                Message.role,
                                Message.content,
                                Message.sequence_number,
                                Message.created_at,
                                Message.updated_at,
                            )
                            .where(
                                Message.conversation_id.in_(
                                    deletion_snapshot_ids
                                )
                            )
                            .order_by(
                                Message.conversation_id,
                                Message.sequence_number,
                            )
                        )
                    ).all()
                return (
                    owner_user,
                    {
                        row.id: tuple(row)
                        for row in conversation_rows
                    },
                    [tuple(row) for row in message_rows],
                )

            state_before_delete = await deletion_state()
            target_before_delete = state_before_delete[1][deletion_target_id]
            target_messages_before_delete = [
                row
                for row in state_before_delete[2]
                if row[1] == deletion_target_id
            ]
            assert target_before_delete[1] == user_id
            assert target_before_delete[3] == 3
            assert [
                row[4] for row in target_messages_before_delete
            ] == [1, 2]
            assert {
                row[0] for row in target_messages_before_delete
            } == {
                UUID(deletion_target_payload["initial_message"]["id"]),
                UUID(deletion_target_append_payload["id"]),
            }

            foreign_delete = await client.delete(
                f"/api/v1/conversations/{deletion_target_id}",
                headers={
                    "Authorization": (
                        f"Bearer {second_user_payload['access_token']}"
                    )
                },
            )
            missing_delete = await client.delete(
                f"/api/v1/conversations/{uuid4()}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            expected_delete_not_found = {
                "code": "HTTP_ERROR",
                "message": "Conversation not found",
            }
            assert foreign_delete.status_code == 404
            assert missing_delete.status_code == 404
            assert foreign_delete.json()["error"] == expected_delete_not_found
            assert missing_delete.json()["error"] == expected_delete_not_found
            assert await deletion_state() == state_before_delete

            owner_delete = await client.delete(
                f"/api/v1/conversations/{deletion_target_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            assert owner_delete.status_code == 204
            assert owner_delete.content == b""

            deleted_get = await client.get(
                f"/api/v1/conversations/{deletion_target_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            assert deleted_get.status_code == 404
            assert deleted_get.json()["error"] == expected_delete_not_found

            deleted_message_history = await client.get(
                f"/api/v1/conversations/{deletion_target_id}/messages",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            assert deleted_message_history.status_code == 200
            assert deleted_message_history.json() == {
                "items": [],
                "next_cursor": None,
            }

            listing_after_delete = await client.get(
                "/api/v1/conversations",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            assert listing_after_delete.status_code == 200
            assert deletion_target_id not in {
                UUID(item["id"])
                for item in listing_after_delete.json()["items"]
            }

            existing_get_after_delete = await client.get(
                f"/api/v1/conversations/{conversation_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            assert existing_get_after_delete.status_code == 200
            assert existing_get_after_delete.json()["title"] == rename_title

            existing_message_history_after_delete = await client.get(
                f"/api/v1/conversations/{conversation_id}/messages",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            assert existing_message_history_after_delete.status_code == 200
            assert [
                item["sequence_number"]
                for item in existing_message_history_after_delete.json()["items"]
            ] == [1, 2]

            state_after_delete = await deletion_state()
            assert state_after_delete[0] == state_before_delete[0]
            assert deletion_target_id not in state_after_delete[1]
            assert {
                conversation_id: row
                for conversation_id, row in state_after_delete[1].items()
            } == {
                conversation_id: row
                for conversation_id, row in state_before_delete[1].items()
                if conversation_id != deletion_target_id
            }
            assert state_after_delete[2] == [
                row
                for row in state_before_delete[2]
                if row[1] != deletion_target_id
            ]
    finally:
        if previous_factory is missing:
            delattr(app.state, "db_session_factory")
        else:
            app.state.db_session_factory = previous_factory

    conversation_id = UUID(created_payload["id"])
    initial_message_payload = created_payload["initial_message"]
    assert set(created_payload) == {
        "id",
        "title",
        "created_at",
        "updated_at",
        "initial_message",
    }
    assert created_payload["title"] == "  API integration  "
    assert set(initial_message_payload) == {
        "id",
        "conversation_id",
        "role",
        "content",
        "sequence_number",
        "created_at",
        "updated_at",
    }
    assert initial_message_payload["conversation_id"] == str(conversation_id)
    assert initial_message_payload["role"] == "user"
    assert initial_message_payload["content"] == "  Exact API content  "
    assert initial_message_payload["sequence_number"] == 1

    assert set(appended_payload) == {
        "id",
        "conversation_id",
        "role",
        "content",
        "sequence_number",
        "created_at",
        "updated_at",
    }
    assert appended_payload["conversation_id"] == str(conversation_id)
    assert appended_payload["role"] == "user"
    assert appended_payload["content"] == "  Exact API follow-up  "
    assert appended_payload["sequence_number"] == 2

    assert [
        item["sequence_number"] for item in first_message_page_payload["items"]
    ] == [1]
    assert first_message_page_payload["next_cursor"] == 1
    assert [
        item["sequence_number"] for item in second_message_page_payload["items"]
    ] == [2]
    assert second_message_page_payload["next_cursor"] is None
    uniform_empty_page = {"items": [], "next_cursor": None}
    assert empty_message_page_payload == uniform_empty_page
    assert missing_message_page_payload == uniform_empty_page
    assert foreign_message_page_payload == uniform_empty_page

    first_conversation_items = first_conversation_page_payload["items"]
    second_conversation_items = second_conversation_page_payload["items"]
    listed_conversation_items = first_conversation_items + second_conversation_items
    listed_conversation_ids = [
        UUID(item["id"]) for item in listed_conversation_items
    ]
    expected_conversation_ids = sorted(
        newer_ids,
        key=lambda value: value.int,
        reverse=True,
    ) + sorted(
        older_ids,
        key=lambda value: value.int,
        reverse=True,
    )
    assert listed_conversation_ids == expected_conversation_ids
    assert len(set(listed_conversation_ids)) == len(listed_conversation_ids)
    listed_ordering_keys = [
        (
            datetime.fromisoformat(item["updated_at"].replace("Z", "+00:00")),
            UUID(item["id"]).int,
        )
        for item in listed_conversation_items
    ]
    assert listed_ordering_keys == sorted(listed_ordering_keys, reverse=True)
    assert first_conversation_page_payload["next_cursor"] == {
        "updated_at": first_conversation_items[-1]["updated_at"],
        "id": first_conversation_items[-1]["id"],
    }
    assert second_conversation_page_payload["next_cursor"] is None
    assert [
        UUID(item["id"]) for item in foreign_conversation_page_payload["items"]
    ] == [UUID(foreign_conversation_payload["id"])]
    assert foreign_conversation_page_payload["next_cursor"] is None
    assert empty_conversation_page_payload == uniform_empty_page
    for item in listed_conversation_items:
        assert set(item) == {"id", "title", "created_at", "updated_at"}
    renamed_list_item = next(
        item
        for item in listed_conversation_items
        if UUID(item["id"]) == conversation_id
    )
    assert renamed_list_item["title"] == rename_title

    async with AsyncSession(
        test_database_engine,
        expire_on_commit=False,
    ) as verification_session:
        stored_user = await verification_session.get(User, user_id)
        stored_conversation = await verification_session.get(
            Conversation,
            conversation_id,
        )
        stored_initial_message = await verification_session.get(
            Message,
            UUID(initial_message_payload["id"]),
        )
        stored_appended_message = await verification_session.get(
            Message,
            UUID(appended_payload["id"]),
        )
        stored_empty_conversation = await verification_session.get(
            Conversation,
            empty_conversation.id,
        )
        after_listing_rows = (
            await verification_session.execute(
                sa.select(
                    Conversation.id,
                    Conversation.owner_id,
                    Conversation.updated_at,
                    Conversation.next_message_sequence,
                ).where(Conversation.id.in_(owned_conversation_ids))
            )
        ).all()
        after_listing = {
            row.id: (
                row.owner_id,
                row.updated_at,
                row.next_message_sequence,
            )
            for row in after_listing_rows
        }

        assert stored_user is not None
        assert stored_user.access_token_digest == digest_access_token(access_token)
        assert stored_user.access_token_digest != access_token
        assert stored_conversation is not None
        assert stored_conversation.owner_id == user_id
        assert stored_conversation.title == rename_title
        assert stored_conversation.next_message_sequence == 3
        assert stored_empty_conversation is not None
        assert stored_empty_conversation.owner_id == user_id
        assert stored_empty_conversation.next_message_sequence == 1
        assert after_listing == before_listing
        assert stored_initial_message is not None
        assert stored_initial_message.conversation_id == stored_conversation.id
        assert stored_initial_message.role is MessageRole.USER
        assert stored_initial_message.content == "  Exact API content  "
        assert stored_initial_message.sequence_number == 1

        assert stored_appended_message is not None
        assert stored_appended_message.conversation_id == stored_conversation.id
        assert stored_appended_message.role is MessageRole.USER
        assert stored_appended_message.content == "  Exact API follow-up  "
        assert stored_appended_message.sequence_number == 2

        first_page = await MessageService(verification_session).list_for_owner(
            stored_conversation.owner_id,
            stored_conversation.id,
            MessagePagination(limit=1),
        )
        assert [message.sequence_number for message in first_page.items] == [1]
        assert first_page.next_cursor is not None
        second_page = await MessageService(verification_session).list_for_owner(
            stored_conversation.owner_id,
            stored_conversation.id,
            MessagePagination(limit=1, cursor=first_page.next_cursor),
        )
        assert [message.sequence_number for message in second_page.items] == [2]
        assert second_page.next_cursor is None

        second_owner_conversations = await ConversationService(
            verification_session
        ).list_for_owner(UUID(second_user_payload["id"]))
        assert [
            conversation.id for conversation in second_owner_conversations.items
        ] == [UUID(foreign_conversation_payload["id"])]


@pytest.mark.asyncio
async def test_authenticated_local_model_listing_is_database_read_only(
    test_database_engine: AsyncEngine,
):
    class FakeLocalRuntime:
        runtime_id = "integration-local"

        def __init__(self) -> None:
            self.discovery_calls = 0

        async def discover_models(self) -> tuple[RuntimeModel, ...]:
            self.discovery_calls += 1
            return (
                RuntimeModel(
                    reference="/private/runtime/model:32b",
                    display_name="Integration 32B",
                    family="IntegrationFamily",
                    parameter_class="32B",
                    capabilities=("chat", "text-generation"),
                ),
            )

    def normalized_schema(value):
        if isinstance(value, dict):
            return tuple(
                sorted(
                    (str(key), normalized_schema(item))
                    for key, item in value.items()
                )
            )
        if isinstance(value, (list, tuple, set)):
            return tuple(sorted(repr(normalized_schema(item)) for item in value))
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        return str(value)

    async def database_state() -> tuple:
        async with AsyncSession(test_database_engine) as session:
            users = (
                await session.execute(
                    sa.select(
                        User.id,
                        User.access_token_digest,
                        User.created_at,
                        User.updated_at,
                    ).order_by(User.id)
                )
            ).all()
            conversations = (
                await session.execute(
                    sa.select(
                        Conversation.id,
                        Conversation.owner_id,
                        Conversation.title,
                        Conversation.next_message_sequence,
                        Conversation.created_at,
                        Conversation.updated_at,
                    ).order_by(Conversation.id)
                )
            ).all()
            messages = (
                await session.execute(
                    sa.select(
                        Message.id,
                        Message.conversation_id,
                        Message.role,
                        Message.content,
                        Message.sequence_number,
                        Message.created_at,
                        Message.updated_at,
                    ).order_by(Message.id)
                )
            ).all()
        return (
            tuple(tuple(row) for row in users),
            tuple(tuple(row) for row in conversations),
            tuple(tuple(row) for row in messages),
        )

    session_factory = async_sessionmaker(
        test_database_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    runtime = FakeLocalRuntime()
    catalog = ModelCatalog((runtime,))
    missing = object()
    previous_factory = getattr(app.state, "db_session_factory", missing)
    previous_catalog = getattr(app.state, "model_catalog", missing)
    app.state.db_session_factory = session_factory
    app.state.model_catalog = catalog

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            provisioned = await client.post("/api/v1/users")
            assert provisioned.status_code == 201
            access_token = provisioned.json()["access_token"]

            schema_before = normalized_schema(
                await _schema_snapshot(test_database_engine)
            )
            state_before = await database_state()

            unauthenticated = await client.get("/api/v1/ai/models")
            assert unauthenticated.status_code == 401
            assert runtime.discovery_calls == 0

            authenticated = await client.get(
                "/api/v1/ai/models",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            assert authenticated.status_code == 200
            payload = authenticated.json()
            assert len(payload["items"]) == 1
            model = payload["items"][0]
            assert model == {
                "model_id": model["model_id"],
                "display_name": "Integration 32B",
                "runtime_id": "integration-local",
                "modality": "text",
                "family": "IntegrationFamily",
                "parameter_class": "32B",
                "capabilities": ["chat", "text_generation"],
                "context_window": None,
                "quantization": None,
                "estimated_vram_bytes": None,
                "availability": "available",
            }
            assert model["model_id"].startswith("integration-local:")
            assert "/private/runtime/model:32b" not in authenticated.text
            assert runtime.discovery_calls == 1

            assert await database_state() == state_before
            assert normalized_schema(
                await _schema_snapshot(test_database_engine)
            ) == schema_before
    finally:
        if previous_factory is missing:
            delattr(app.state, "db_session_factory")
        else:
            app.state.db_session_factory = previous_factory
        if previous_catalog is missing:
            delattr(app.state, "model_catalog")
        else:
            app.state.model_catalog = previous_catalog


@pytest.mark.asyncio
async def test_authenticated_conversation_generation_is_owner_scoped_and_stale_safe(
    test_database_engine: AsyncEngine,
    monkeypatch,
):
    class FakeLocalTextRuntime:
        runtime_id = "integration-local"

        def __init__(self) -> None:
            self.mode = "success"
            self.discovery_calls = 0
            self.generation_calls: list[tuple] = []
            self.stale_owner_id: UUID | None = None
            self.stale_conversation_id: UUID | None = None

        async def discover_models(self) -> tuple[RuntimeModel, ...]:
            self.discovery_calls += 1
            return (
                RuntimeModel(
                    reference="/private/runtime/model:70b",
                    display_name="Integration 70B",
                    parameter_class="70B+",
                    capabilities=("chat", "text-generation"),
                    availability=(
                        ModelAvailability.UNAVAILABLE
                        if self.mode == "descriptor-unavailable"
                        else ModelAvailability.AVAILABLE
                    ),
                ),
            )

        async def generate_text(
            self,
            runtime_reference,
            messages,
            *,
            max_output_tokens,
            temperature=None,
            seed=None,
            top_p=None,
            top_k=None,
            min_p=None,
            repeat_penalty=None,
            repeat_last_n=None,
            typical_p=None,
            presence_penalty=None,
            frequency_penalty=None,
        ) -> TextGenerationResult:
            self.generation_calls.append(
                (
                    runtime_reference,
                    messages,
                    max_output_tokens,
                    temperature,
                    seed,
                    top_p,
                    top_k,
                    min_p,
                    repeat_penalty,
                    repeat_last_n,
                    typical_p,
                    presence_penalty,
                    frequency_penalty,
                )
            )
            if self.mode == "unavailable":
                raise TextGenerationRuntimeUnavailableError(
                    "secret local runtime detail"
                )
            if self.mode == "stale":
                assert self.stale_owner_id is not None
                assert self.stale_conversation_id is not None
                async with AsyncSession(
                    test_database_engine,
                    expire_on_commit=False,
                ) as concurrent_session:
                    intervening = await MessageService(
                        concurrent_session
                    ).append_for_owner(
                        self.stale_owner_id,
                        self.stale_conversation_id,
                        MessageRole.USER,
                        "intervening user message",
                    )
                    assert intervening is not None
            return TextGenerationResult(content="  exact local answer  ")

    def normalized_schema(value):
        if isinstance(value, dict):
            return tuple(
                sorted(
                    (str(key), normalized_schema(item))
                    for key, item in value.items()
                )
            )
        if isinstance(value, (list, tuple, set)):
            return tuple(sorted(repr(normalized_schema(item)) for item in value))
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        return str(value)

    async def persisted_conversation(conversation_id: UUID):
        async with AsyncSession(
            test_database_engine,
            expire_on_commit=False,
        ) as session:
            conversation = await session.get(Conversation, conversation_id)
            messages = (
                await session.execute(
                    sa.select(Message)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.sequence_number)
                )
            ).scalars().all()
            return conversation, tuple(messages)

    runtime = FakeLocalTextRuntime()

    class PreContextRaceMessageService(MessageService):
        async def list_generation_context_for_owner(
            self,
            owner_id,
            conversation_id,
            *,
            max_messages,
        ):
            if runtime.mode == "pre-context-stale":
                runtime.mode = "success"
                async with AsyncSession(
                    test_database_engine,
                    expire_on_commit=False,
                ) as concurrent_session:
                    intervening = await MessageService(
                        concurrent_session
                    ).append_for_owner(
                        owner_id,
                        conversation_id,
                        MessageRole.USER,
                        "pre-context intervening user message",
                    )
                    assert intervening is not None
            return await super().list_generation_context_for_owner(
                owner_id,
                conversation_id,
                max_messages=max_messages,
            )

    monkeypatch.setattr(
        generation_module,
        "MessageService",
        PreContextRaceMessageService,
    )
    catalog = ModelCatalog((runtime,))
    generation_router = TextGenerationRouter((runtime,))
    session_factory = async_sessionmaker(
        test_database_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    missing = object()
    previous_factory = getattr(app.state, "db_session_factory", missing)
    previous_catalog = getattr(app.state, "model_catalog", missing)
    previous_router = getattr(app.state, "text_generation_router", missing)
    app.state.db_session_factory = session_factory
    app.state.model_catalog = catalog
    app.state.text_generation_router = generation_router

    try:
        schema_before = normalized_schema(
            await _schema_snapshot(test_database_engine)
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            owner_response = await client.post("/api/v1/users")
            foreign_response = await client.post("/api/v1/users")
            assert owner_response.status_code == 201
            assert foreign_response.status_code == 201
            owner = owner_response.json()
            foreign = foreign_response.json()
            owner_headers = {
                "Authorization": f"Bearer {owner['access_token']}"
            }
            foreign_headers = {
                "Authorization": f"Bearer {foreign['access_token']}"
            }

            owner_created = await client.post(
                "/api/v1/conversations",
                headers=owner_headers,
                json={
                    "title": "Generation target",
                    "system_prompt": "  exact system prompt  ",
                    "initial_message": "first user prompt",
                },
            )
            foreign_created = await client.post(
                "/api/v1/conversations",
                headers=foreign_headers,
                json={
                    "title": "Foreign generation target",
                    "initial_message": "foreign prompt",
                },
            )
            assert owner_created.status_code == 201
            assert foreign_created.status_code == 201
            conversation_id = UUID(owner_created.json()["id"])
            foreign_conversation_id = UUID(foreign_created.json()["id"])

            unauthenticated = await client.post(
                f"/api/v1/conversations/{conversation_id}/messages/generate",
                json={
                    "model_id": f"integration-local:{'a' * 24}",
                    "user_message": "must not persist",
                },
            )
            assert unauthenticated.status_code == 401
            assert runtime.discovery_calls == 0
            assert runtime.generation_calls == []

            models = await client.get(
                "/api/v1/ai/models",
                headers=owner_headers,
            )
            assert models.status_code == 200
            model_id = models.json()["items"][0]["model_id"]

            generated = await client.post(
                f"/api/v1/conversations/{conversation_id}/messages/generate",
                headers=owner_headers,
                json={
                    "model_id": model_id,
                    "user_message": "  second user prompt  ",
                    "max_output_tokens": 128,
                    "temperature": 0.25,
                    "seed": 42,
                    "top_p": 0.9,
                    "top_k": 40,
                    "min_p": 0.05,
                    "repeat_penalty": 1.1,
                    "repeat_last_n": 64,
                    "typical_p": 0.7,
                    "presence_penalty": 1.5,
                    "frequency_penalty": 0.75,
                },
            )
            assert generated.status_code == 201
            assert generated.json() == {
                "model_id": model_id,
                "message": {
                    "id": generated.json()["message"]["id"],
                    "conversation_id": str(conversation_id),
                    "role": "assistant",
                    "content": "  exact local answer  ",
                    "sequence_number": 4,
                    "created_at": generated.json()["message"]["created_at"],
                    "updated_at": generated.json()["message"]["updated_at"],
                },
            }
            assert "/private/runtime/model:70b" not in generated.text
            (
                runtime_reference,
                context,
                output_bound,
                temperature,
                seed,
                top_p,
                top_k,
                min_p,
                repeat_penalty,
                repeat_last_n,
                typical_p,
                presence_penalty,
                frequency_penalty,
            ) = runtime.generation_calls[0]
            assert runtime_reference == "/private/runtime/model:70b"
            assert [(message.role.value, message.content) for message in context] == [
                ("system", "  exact system prompt  "),
                ("user", "first user prompt"),
                ("user", "  second user prompt  "),
            ]
            assert output_bound == 128
            assert temperature == 0.25
            assert seed == 42
            assert top_p == 0.9
            assert top_k == 40
            assert min_p == 0.05
            assert repeat_penalty == 1.1
            assert repeat_last_n == 64
            assert typical_p == 0.7
            assert presence_penalty == 1.5
            assert frequency_penalty == 0.75

            foreign_attempt = await client.post(
                f"/api/v1/conversations/{foreign_conversation_id}/messages/generate",
                headers=owner_headers,
                json={
                    "model_id": model_id,
                    "user_message": "must not persist for foreign owner",
                },
            )
            missing_attempt = await client.post(
                f"/api/v1/conversations/{uuid4()}/messages/generate",
                headers=owner_headers,
                json={
                    "model_id": model_id,
                    "user_message": "must not persist for missing conversation",
                },
            )
            assert foreign_attempt.status_code == 404
            assert missing_attempt.status_code == 404
            assert foreign_attempt.json()["error"] == missing_attempt.json()["error"]
            assert len(runtime.generation_calls) == 1

            unknown_model = await client.post(
                f"/api/v1/conversations/{conversation_id}/messages/generate",
                headers=owner_headers,
                json={
                    "model_id": f"integration-local:{'f' * 24}",
                    "user_message": "unknown-model user prompt",
                },
            )
            assert unknown_model.status_code == 404
            assert unknown_model.json()["error"]["message"] == "Model not found"
            assert len(runtime.generation_calls) == 1

            runtime.mode = "descriptor-unavailable"
            unavailable_descriptor = await client.post(
                f"/api/v1/conversations/{conversation_id}/messages/generate",
                headers=owner_headers,
                json={"model_id": model_id},
            )
            assert unavailable_descriptor.status_code == 503
            assert unavailable_descriptor.json()["error"]["message"] == (
                "Local model runtime unavailable"
            )
            assert len(runtime.generation_calls) == 1

            before_unavailable, messages_before_unavailable = (
                await persisted_conversation(conversation_id)
            )
            assert before_unavailable is not None
            runtime.mode = "unavailable"
            unavailable = await client.post(
                f"/api/v1/conversations/{conversation_id}/messages/generate",
                headers=owner_headers,
                json={
                    "model_id": model_id,
                    "user_message": "runtime-failure user prompt",
                },
            )
            assert unavailable.status_code == 503
            assert unavailable.json()["error"]["message"] == (
                "Local model runtime unavailable"
            )
            after_unavailable, messages_after_unavailable = (
                await persisted_conversation(conversation_id)
            )
            assert after_unavailable is not None
            assert after_unavailable.next_message_sequence == (
                before_unavailable.next_message_sequence + 1
            )
            assert [
                (message.role, message.content, message.sequence_number)
                for message in messages_after_unavailable
            ] == [
                (message.role, message.content, message.sequence_number)
                for message in messages_before_unavailable
            ] + [
                (
                    MessageRole.USER,
                    "runtime-failure user prompt",
                    before_unavailable.next_message_sequence,
                )
            ]
            assert len(runtime.generation_calls) == 2

            runtime.mode = "success"
            retry = await client.post(
                f"/api/v1/conversations/{conversation_id}/messages/generate",
                headers=owner_headers,
                json={"model_id": model_id},
            )
            assert retry.status_code == 201
            assert retry.json()["message"]["role"] == "assistant"
            assert retry.json()["message"]["sequence_number"] == 7
            assert len(runtime.generation_calls) == 3
            assert runtime.generation_calls[2][2] == 1024
            assert runtime.generation_calls[2][3] is None
            assert runtime.generation_calls[2][4] is None
            assert runtime.generation_calls[2][5] is None
            assert runtime.generation_calls[2][6] is None
            assert runtime.generation_calls[2][7] is None
            assert runtime.generation_calls[2][8] is None
            assert runtime.generation_calls[2][9] is None
            assert runtime.generation_calls[2][10] is None
            assert runtime.generation_calls[2][11] is None
            assert runtime.generation_calls[2][12] is None

            runtime.mode = "pre-context-stale"
            pre_context_stale = await client.post(
                f"/api/v1/conversations/{conversation_id}/messages/generate",
                headers=owner_headers,
                json={
                    "model_id": model_id,
                    "user_message": "pre-context request user message",
                },
            )
            assert pre_context_stale.status_code == 409
            assert pre_context_stale.json()["error"]["message"] == (
                "Conversation changed during generation"
            )
            assert len(runtime.generation_calls) == 3

            runtime.mode = "stale"
            runtime.stale_owner_id = UUID(owner["id"])
            runtime.stale_conversation_id = conversation_id
            stale = await client.post(
                f"/api/v1/conversations/{conversation_id}/messages/generate",
                headers=owner_headers,
                json={
                    "model_id": model_id,
                    "user_message": "during-inference request user message",
                },
            )
            assert stale.status_code == 409
            assert stale.json()["error"]["message"] == (
                "Conversation changed during generation"
            )
            assert len(runtime.generation_calls) == 4

            history_items = []
            cursor = None
            while True:
                parameters = {"limit": 4}
                if cursor is not None:
                    parameters["cursor"] = cursor
                history = await client.get(
                    f"/api/v1/conversations/{conversation_id}/messages",
                    headers=owner_headers,
                    params=parameters,
                )
                assert history.status_code == 200
                history_page = history.json()
                history_items.extend(history_page["items"])
                cursor = history_page["next_cursor"]
                if cursor is None:
                    break

            assert [
                (item["role"], item["content"], item["sequence_number"])
                for item in history_items
            ] == [
                ("system", "  exact system prompt  ", 1),
                ("user", "first user prompt", 2),
                ("user", "  second user prompt  ", 3),
                ("assistant", "  exact local answer  ", 4),
                ("user", "unknown-model user prompt", 5),
                ("user", "runtime-failure user prompt", 6),
                ("assistant", "  exact local answer  ", 7),
                ("user", "pre-context request user message", 8),
                ("user", "pre-context intervening user message", 9),
                ("user", "during-inference request user message", 10),
                ("user", "intervening user message", 11),
            ]
            stored_owner, stored_messages = await persisted_conversation(
                conversation_id
            )
            stored_foreign, foreign_messages = await persisted_conversation(
                foreign_conversation_id
            )
            assert stored_owner is not None
            assert stored_owner.owner_id == UUID(owner["id"])
            assert stored_owner.next_message_sequence == 12
            assert len(stored_messages) == 11
            assert stored_foreign is not None
            assert stored_foreign.owner_id == UUID(foreign["id"])
            assert stored_foreign.next_message_sequence == 2
            assert [
                (message.role, message.content, message.sequence_number)
                for message in foreign_messages
            ] == [(MessageRole.USER, "foreign prompt", 1)]

            listed = await client.get(
                "/api/v1/conversations",
                headers=owner_headers,
            )
            assert listed.status_code == 200
            assert conversation_id in {
                UUID(item["id"]) for item in listed.json()["items"]
            }

        assert normalized_schema(
            await _schema_snapshot(test_database_engine)
        ) == schema_before
    finally:
        if previous_factory is missing:
            delattr(app.state, "db_session_factory")
        else:
            app.state.db_session_factory = previous_factory
        if previous_catalog is missing:
            delattr(app.state, "model_catalog")
        else:
            app.state.model_catalog = previous_catalog
        if previous_router is missing:
            delattr(app.state, "text_generation_router")
        else:
            app.state.text_generation_router = previous_router
