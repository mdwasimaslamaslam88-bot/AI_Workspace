from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Asset, MessageRole
from app.repositories.message import MessageAttachmentClaimError, MessageRepository


def _asset(owner_id, *, content=b"content") -> Asset:
    asset_id = uuid4()
    return Asset(
        id=asset_id,
        owner_id=owner_id,
        original_filename="document.txt",
        media_type="text/plain",
        byte_size=len(content),
        content_sha256="a" * 64,
        storage_key=(
            f"objects/{asset_id.hex[:2]}/{asset_id.hex[2:4]}/{asset_id.hex}"
        ),
        upload_idempotency_key=uuid4(),
    )


def _allocation_result(sequence_number: int):
    result = Mock()
    result.scalar_one_or_none.return_value = sequence_number
    return result


def _assets_result(assets):
    result = Mock()
    result.scalars.return_value = list(assets)
    return result


@pytest.mark.asyncio
async def test_claims_owned_active_unattached_assets_in_request_order():
    owner_id = uuid4()
    conversation_id = uuid4()
    first = _asset(owner_id)
    second = _asset(owner_id)
    session = AsyncMock(spec=AsyncSession)
    session.execute.side_effect = [
        _allocation_result(4),
        _assets_result((second, first)),
    ]

    message = await MessageRepository(session).append_for_owner(
        owner_id,
        conversation_id,
        MessageRole.USER,
        "text remains text-only",
        attachment_ids=(first.id, second.id),
    )

    assert message is not None
    assert message.content == "text remains text-only"
    assert [link.asset.id for link in message.asset_links] == [first.id, second.id]
    assert [link.position for link in message.asset_links] == [1, 2]
    claim_statement = session.execute.await_args_list[1].args[0]
    compiled = claim_statement.compile(dialect=postgresql.dialect())
    sql = " ".join(str(compiled).lower().split())
    assert "assets.owner_id =" in sql
    assert "assets.deleted_at is null" in sql
    assert "message_assets.asset_id is null" in sql
    assert "for update of assets" in sql
    assert owner_id in compiled.params.values()
    session.flush.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_any_unavailable_asset_rejects_the_entire_claim_before_insert():
    owner_id = uuid4()
    available = _asset(owner_id)
    unavailable_id = uuid4()
    session = AsyncMock(spec=AsyncSession)
    session.execute.side_effect = [
        _allocation_result(1),
        _assets_result((available,)),
    ]

    with pytest.raises(MessageAttachmentClaimError):
        await MessageRepository(session).append_for_owner(
            owner_id,
            uuid4(),
            MessageRole.USER,
            "all or nothing",
            attachment_ids=(available.id, unavailable_id),
        )

    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_attachment_ids_are_rejected_before_database_work():
    asset_id = uuid4()
    session = AsyncMock(spec=AsyncSession)

    with pytest.raises(ValueError, match="attachment_ids must be unique"):
        await MessageRepository(session).append_for_owner(
            uuid4(),
            uuid4(),
            MessageRole.USER,
            "duplicate",
            attachment_ids=(asset_id, asset_id),
        )

    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_vision_metadata_is_owner_message_scoped_and_position_ordered():
    owner_id = uuid4()
    conversation_id = uuid4()
    message_id = uuid4()
    first_id = uuid4()
    second_id = uuid4()
    result = Mock()
    result.all.return_value = [
        SimpleNamespace(
            asset_id=first_id,
            position=1,
            media_type="image/png",
            byte_size=11,
            content_sha256="a" * 64,
            storage_key="objects/first",
        ),
        SimpleNamespace(
            asset_id=second_id,
            position=2,
            media_type="image/jpeg",
            byte_size=13,
            content_sha256="b" * 64,
            storage_key="objects/second",
        ),
    ]
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = result

    metadata = await MessageRepository(
        session
    ).list_attachment_metadata_for_owner_message(
        owner_id,
        conversation_id,
        message_id,
        (first_id, second_id),
    )

    assert [item.asset_id for item in metadata] == [first_id, second_id]
    assert [item.position for item in metadata] == [1, 2]
    assert [item.media_type for item in metadata] == ["image/png", "image/jpeg"]
    statement = session.execute.await_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = " ".join(str(compiled).lower().split())
    assert "join messages on messages.id = message_assets.message_id" in sql
    assert "join conversations on conversations.id = messages.conversation_id" in sql
    assert "join assets on assets.id = message_assets.asset_id" in sql
    assert "messages.conversation_id =" in sql
    assert "conversations.owner_id =" in sql
    assert "assets.owner_id =" in sql
    assert "assets.deleted_at is null" in sql
    assert "order by message_assets.position asc" in sql
    assert owner_id in compiled.params.values()
    assert conversation_id in compiled.params.values()
    assert message_id in compiled.params.values()


@pytest.mark.asyncio
async def test_vision_metadata_requires_exact_requested_attachment_order():
    first_id = uuid4()
    second_id = uuid4()
    result = Mock()
    result.all.return_value = [
        SimpleNamespace(
            asset_id=first_id,
            position=1,
            media_type="image/png",
            byte_size=1,
            content_sha256="a" * 64,
            storage_key="objects/first",
        )
    ]
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = result

    with pytest.raises(
        MessageAttachmentClaimError,
        match="one or more attachments are unavailable",
    ):
        await MessageRepository(
            session
        ).list_attachment_metadata_for_owner_message(
            uuid4(),
            uuid4(),
            uuid4(),
            (first_id, second_id),
        )
