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
async def test_create_with_initial_message_persists_one_atomic_conversation(
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
        assert message.sequence_number == 1

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
                    )
                )
            )
            .scalars()
            .all()
        )

        assert len(conversations) == 1
        assert conversations[0].id == conversation_id
        assert conversations[0].next_message_sequence == 2
        assert len(messages) == 1
        assert messages[0].id == message_id
        assert messages[0].conversation_id == conversation_id
        assert messages[0].sequence_number == 1


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
