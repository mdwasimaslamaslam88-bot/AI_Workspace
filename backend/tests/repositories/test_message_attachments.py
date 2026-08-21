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
