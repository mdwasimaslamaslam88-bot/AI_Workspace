import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Asset, Conversation, Message, MessageAsset, MessageRole, User
from app.repositories.asset import AssetRepository
from app.schemas.message import MessageResponse
from app.services.asset import AssetService
from app.services.conversation import ConversationService
from app.services.message import (
    MessageAppendConflictError,
    MessageAttachmentUnavailableError,
    MessageService,
)
from app.services.user import UserService
from app.storage.local import LocalAssetStorage


pytestmark = pytest.mark.integration


class BytesStream:
    def __init__(self, content: bytes):
        self.content = content

    async def read(self, _size=-1):
        content, self.content = self.content, b""
        return content


def _storage_key(asset_id):
    value = asset_id.hex
    return f"objects/{value[:2]}/{value[2:4]}/{value}"


async def _create_user(engine: AsyncEngine) -> User:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        return await UserService(session).create(User())


async def _create_conversation(engine: AsyncEngine, owner_id):
    async with AsyncSession(engine, expire_on_commit=False) as session:
        created = await ConversationService(
            session
        ).create_with_initial_message_for_owner(
            owner_id,
            None,
            MessageRole.USER,
            "initial",
        )
        assert created is not None
        return created[0]


async def _insert_asset(session, owner_id, *, deleted=False):
    asset_id = uuid4()
    created_at = datetime.now(timezone.utc)
    asset = Asset(
        id=asset_id,
        owner_id=owner_id,
        original_filename="private.txt",
        media_type="text/plain",
        byte_size=7,
        content_sha256="a" * 64,
        storage_key=_storage_key(asset_id),
        upload_idempotency_key=uuid4(),
        created_at=created_at,
        deleted_at=created_at if deleted else None,
    )
    session.add(asset)
    await session.commit()
    return asset


@pytest.mark.asyncio
async def test_owned_asset_migration_has_exact_constraints(test_database_engine):
    def inspect(connection):
        inspector = sa.inspect(connection)
        return {
            "asset_columns": inspector.get_columns("assets"),
            "asset_checks": inspector.get_check_constraints("assets"),
            "asset_fks": inspector.get_foreign_keys("assets"),
            "asset_uniques": inspector.get_unique_constraints("assets"),
            "asset_indexes": inspector.get_indexes("assets"),
            "link_columns": inspector.get_columns("message_assets"),
            "link_checks": inspector.get_check_constraints("message_assets"),
            "link_fks": inspector.get_foreign_keys("message_assets"),
            "link_uniques": inspector.get_unique_constraints("message_assets"),
            "link_pk": inspector.get_pk_constraint("message_assets"),
        }

    async with test_database_engine.connect() as connection:
        snapshot = await connection.run_sync(inspect)

    assert [item["name"] for item in snapshot["asset_columns"]] == [
        "id",
        "owner_id",
        "original_filename",
        "media_type",
        "byte_size",
        "content_sha256",
        "storage_key",
        "upload_idempotency_key",
        "created_at",
        "deleted_at",
    ]
    assert {item["name"] for item in snapshot["asset_checks"]} == {
        "ck_assets_byte_size_positive",
        "ck_assets_content_sha256_lowercase_hex",
        "ck_assets_storage_key_generated",
        "ck_assets_deleted_at_not_before_created_at",
    }
    assert snapshot["asset_fks"] == [
        {
            **snapshot["asset_fks"][0],
            "name": "fk_assets_owner_id_users",
            "constrained_columns": ["owner_id"],
            "referred_table": "users",
            "referred_columns": ["id"],
            "options": {"ondelete": "RESTRICT"},
        }
    ]
    assert {tuple(item["column_names"]) for item in snapshot["asset_uniques"]} == {
        ("storage_key",),
        ("owner_id", "upload_idempotency_key"),
    }
    indexes = {item["name"]: item for item in snapshot["asset_indexes"]}
    assert indexes["ix_assets_owner_created_at_id"]["column_names"] == [
        "owner_id",
        "created_at",
        "id",
    ]
    assert indexes["ix_assets_deleted_at"]["dialect_options"]["postgresql_where"]

    assert [item["name"] for item in snapshot["link_columns"]] == [
        "message_id",
        "asset_id",
        "position",
    ]
    assert {item["name"] for item in snapshot["link_checks"]} == {
        "ck_message_assets_position_positive"
    }
    assert snapshot["link_pk"]["constrained_columns"] == ["message_id", "asset_id"]
    assert {tuple(item["column_names"]) for item in snapshot["link_uniques"]} == {
        ("message_id", "position"),
        ("asset_id",),
    }
    foreign_keys = {item["name"]: item for item in snapshot["link_fks"]}
    assert foreign_keys["fk_message_assets_message_id_messages"]["options"] == {
        "ondelete": "CASCADE"
    }
    assert foreign_keys["fk_message_assets_asset_id_assets"]["options"] == {
        "ondelete": "RESTRICT"
    }


@pytest.mark.asyncio
async def test_database_enforces_asset_and_attachment_constraints(test_database_engine):
    owner = await _create_user(test_database_engine)
    conversation = await _create_conversation(test_database_engine, owner.id)

    async def rejected_asset(**changes):
        async with AsyncSession(test_database_engine) as session:
            asset_id = uuid4()
            values = {
                "id": asset_id,
                "owner_id": owner.id,
                "original_filename": None,
                "media_type": "application/octet-stream",
                "byte_size": 1,
                "content_sha256": "a" * 64,
                "storage_key": _storage_key(asset_id),
                "upload_idempotency_key": uuid4(),
                "created_at": datetime.now(timezone.utc),
                "deleted_at": None,
            }
            values.update(changes)
            session.add(Asset(**values))
            with pytest.raises(IntegrityError):
                await session.commit()

    await rejected_asset(byte_size=0)
    await rejected_asset(content_sha256="A" * 64)
    await rejected_asset(storage_key="../../escape")
    now = datetime.now(timezone.utc)
    await rejected_asset(created_at=now, deleted_at=now - timedelta(seconds=1))

    async with AsyncSession(test_database_engine, expire_on_commit=False) as session:
        first = await _insert_asset(session, owner.id)
        second = await _insert_asset(session, owner.id)
        message = await MessageService(session).append_for_owner(
            owner.id,
            conversation.id,
            MessageRole.USER,
            "attached",
            attachment_ids=(first.id,),
        )
        assert message is not None
        message_id = message.id
        second_id = second.id
        session.add(MessageAsset(message_id=message_id, asset_id=second_id, position=1))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
        session.add(MessageAsset(message_id=message_id, asset_id=second_id, position=0))
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_mixed_owner_deleted_and_reused_claims_roll_back_atomically(
    test_database_engine,
):
    owner = await _create_user(test_database_engine)
    foreign_owner = await _create_user(test_database_engine)
    conversation = await _create_conversation(test_database_engine, owner.id)
    async with AsyncSession(test_database_engine, expire_on_commit=False) as session:
        owned = await _insert_asset(session, owner.id)
        foreign = await _insert_asset(session, foreign_owner.id)
        deleted = await _insert_asset(session, owner.id, deleted=True)
        owned_id = owned.id
        foreign_id = foreign.id
        deleted_id = deleted.id

        with pytest.raises(MessageAttachmentUnavailableError):
            await MessageService(session).append_for_owner(
                owner.id,
                conversation.id,
                MessageRole.USER,
                "must roll back",
                attachment_ids=(owned_id, foreign_id),
            )
        assert await session.scalar(
            sa.select(sa.func.count()).select_from(MessageAsset).where(
                MessageAsset.asset_id == owned_id
            )
        ) == 0

        with pytest.raises(MessageAttachmentUnavailableError):
            await MessageService(session).append_for_owner(
                owner.id,
                conversation.id,
                MessageRole.USER,
                "deleted",
                attachment_ids=(deleted_id,),
            )

        attached = await MessageService(session).append_for_owner(
            owner.id,
            conversation.id,
            MessageRole.USER,
            "ordered",
            attachment_ids=(owned_id,),
        )
        assert attached is not None
        with pytest.raises(MessageAttachmentUnavailableError):
            await MessageService(session).append_for_owner(
                owner.id,
                conversation.id,
                MessageRole.USER,
                "reused",
                attachment_ids=(owned_id,),
            )


@pytest.mark.asyncio
async def test_concurrent_claim_has_exactly_one_winner(test_database_engine):
    owner = await _create_user(test_database_engine)
    conversation = await _create_conversation(test_database_engine, owner.id)
    async with AsyncSession(test_database_engine, expire_on_commit=False) as session:
        asset = await _insert_asset(session, owner.id)

    async def claim(label):
        async with AsyncSession(test_database_engine, expire_on_commit=False) as session:
            try:
                return await MessageService(session).append_for_owner(
                    owner.id,
                    conversation.id,
                    MessageRole.USER,
                    label,
                    attachment_ids=(asset.id,),
                )
            except (MessageAttachmentUnavailableError, MessageAppendConflictError) as exc:
                return exc

    results = await asyncio.gather(claim("first"), claim("second"))
    assert sum(isinstance(result, Message) for result in results) == 1
    async with AsyncSession(test_database_engine) as session:
        assert await session.scalar(
            sa.select(sa.func.count()).select_from(MessageAsset).where(
                MessageAsset.asset_id == asset.id
            )
        ) == 1


@pytest.mark.asyncio
async def test_concurrent_idempotent_upload_returns_one_asset_and_one_file(
    test_database_engine,
    tmp_path,
):
    owner = await _create_user(test_database_engine)
    storage = LocalAssetStorage(tmp_path / "assets")
    key = uuid4()

    async def upload():
        async with AsyncSession(test_database_engine, expire_on_commit=False) as session:
            return await AssetService(session, storage).upload_for_owner(
                owner.id,
                key,
                filename="same.txt",
                claimed_media_type="text/plain",
                stream=BytesStream(b"identical"),
            )

    first, second = await asyncio.gather(upload(), upload())
    assert first.asset.id == second.asset.id
    assert {first.created, second.created} == {True, False}
    assert len([path for path in storage.objects_root.rglob("*") if path.is_file()]) == 1
    async with AsyncSession(test_database_engine) as session:
        assert await session.scalar(
            sa.select(sa.func.count()).select_from(Asset).where(
                Asset.owner_id == owner.id,
                Asset.upload_idempotency_key == key,
            )
        ) == 1


@pytest.mark.asyncio
async def test_delete_tombstone_redacts_message_and_conversation_delete_cleans_bytes(
    test_database_engine,
    tmp_path,
):
    owner = await _create_user(test_database_engine)
    conversation = await _create_conversation(test_database_engine, owner.id)
    storage = LocalAssetStorage(tmp_path / "assets")
    async with AsyncSession(test_database_engine, expire_on_commit=False) as session:
        uploaded = await AssetService(session, storage).upload_for_owner(
            owner.id,
            uuid4(),
            filename="private.txt",
            claimed_media_type="text/plain",
            stream=BytesStream(b"private"),
        )
        message = await MessageService(session).append_for_owner(
            owner.id,
            conversation.id,
            MessageRole.USER,
            "attachment",
            attachment_ids=(uploaded.asset.id,),
        )
        assert message is not None
        assert await AssetService(session, storage).delete_for_owner(
            owner.id,
            uploaded.asset.id,
        )
        persisted = await session.scalar(
            sa.select(Message)
            .where(Message.id == message.id)
            .options(selectinload(Message.asset_links))
        )
        assert persisted is not None
        response = MessageResponse.model_validate(persisted)
        assert response.attachments[0].state == "deleted"
        assert response.attachments[0].original_filename is None
        assert response.attachments[0].media_type is None
        assert response.attachments[0].byte_size is None

        second = await AssetService(session, storage).upload_for_owner(
            owner.id,
            uuid4(),
            filename="conversation.txt",
            claimed_media_type="text/plain",
            stream=BytesStream(b"conversation"),
        )
        second_message = await MessageService(session).append_for_owner(
            owner.id,
            conversation.id,
            MessageRole.USER,
            "second attachment",
            attachment_ids=(second.asset.id,),
        )
        assert second_message is not None
        second_id = second.asset.id
        second_storage_key = second.asset.storage_key
        assert await ConversationService(session, storage).delete_for_owner(
            owner.id,
            conversation.id,
        )
        assert not storage.path_for(second_storage_key).exists()
        tombstone_deleted_at = await session.scalar(
            sa.select(Asset.deleted_at).where(Asset.id == second_id)
        )
        assert tombstone_deleted_at is not None
        assert await session.get(Conversation, conversation.id) is None
        assert await session.scalar(
            sa.select(sa.func.count()).select_from(MessageAsset).where(
                MessageAsset.asset_id == second_id
            )
        ) == 0
