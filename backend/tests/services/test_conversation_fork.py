import hashlib
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Asset,
    AssetProvenanceKind,
    Conversation,
    Message,
    MessageAsset,
    MessageRole,
)
from app.services.conversation_fork import (
    MAX_CONVERSATION_FORK_ASSET_BYTES,
    MAX_CONVERSATION_FORK_CONTENT_CHARACTERS,
    ConversationForkInvalidError,
    ConversationForkNotFoundError,
    ConversationForkService,
    ConversationForkStorageError,
    ConversationForkTooLargeError,
)
from app.storage.local import LocalAssetStorage


def _result(*, scalar=None, rows=()):
    result = Mock()
    result.scalar_one_or_none.return_value = scalar
    result.scalars.return_value.unique.return_value.all.return_value = list(rows)
    return result


def _source_snapshot(storage, owner_id, *, content=b"private image"):
    asset_id = uuid4()
    writer = storage.begin_write(asset_id)
    writer.write(content)
    storage_key = writer.finalize()
    asset = Asset(
        id=asset_id,
        owner_id=owner_id,
        original_filename="owner.png",
        media_type="image/png",
        byte_size=len(content),
        content_sha256=hashlib.sha256(content).hexdigest(),
        storage_key=storage_key,
        upload_idempotency_key=uuid4(),
        provenance_kind=AssetProvenanceKind.UPLOAD,
        source_asset_id=None,
        runtime_id=None,
        model_id=None,
    )
    conversation = Conversation(
        id=uuid4(),
        owner_id=owner_id,
        title="Private history",
        next_message_sequence=3,
        is_pinned=True,
        is_archived=True,
    )
    first = Message(
        id=uuid4(),
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content="original prompt",
        sequence_number=1,
    )
    first.asset_links = [
        MessageAsset(asset_id=asset.id, asset=asset, position=1)
    ]
    first.citation_links = []
    second = Message(
        id=uuid4(),
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content="original answer",
        sequence_number=2,
    )
    second.asset_links = []
    second.citation_links = []
    return conversation, (first, second), asset, content


@pytest.mark.asyncio
async def test_fork_copies_private_assets_and_history_once(tmp_path):
    storage = LocalAssetStorage(tmp_path / "assets")
    owner_id = uuid4()
    source, messages, source_asset, content = _source_snapshot(storage, owner_id)
    session = AsyncMock(spec=AsyncSession)
    session.execute.side_effect = [
        _result(scalar=source),
        _result(rows=messages),
    ]

    fork = await ConversationForkService(session, storage).fork_for_owner(
        owner_id,
        source.id,
    )

    assert fork.id != source.id
    assert fork.owner_id == owner_id
    assert fork.title == "Private history (copy)"
    assert fork.next_message_sequence == 3
    assert fork.is_pinned is False
    assert fork.is_archived is False
    added = session.add_all.call_args.args[0]
    copied_assets = [item for item in added if isinstance(item, Asset)]
    copied_messages = [item for item in added if isinstance(item, Message)]
    assert len(copied_assets) == 1
    assert [item.content for item in copied_messages] == [
        "original prompt",
        "original answer",
    ]
    copied_asset = copied_assets[0]
    assert copied_asset.id != source_asset.id
    assert copied_asset.storage_key != source_asset.storage_key
    assert copied_asset.content_sha256 == source_asset.content_sha256
    assert copied_messages[0].asset_links[0].asset is copied_asset
    with storage.open_read(copied_asset.storage_key) as handle:
        assert handle.read() == content
    session.flush.assert_awaited_once_with()
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()
    first_sql = " ".join(
        str(session.execute.await_args_list[0].args[0]).lower().split()
    )
    assert "conversations.owner_id" in first_sql
    assert "for update" in first_sql


@pytest.mark.asyncio
async def test_fork_replaces_only_final_user_message_without_mutating_source(tmp_path):
    storage = LocalAssetStorage(tmp_path / "assets")
    owner_id = uuid4()
    source, messages, _source_asset, _content = _source_snapshot(storage, owner_id)
    source.next_message_sequence = 2
    source_messages = (messages[0],)
    session = AsyncMock(spec=AsyncSession)
    session.execute.side_effect = [
        _result(scalar=source),
        _result(rows=source_messages),
    ]

    await ConversationForkService(session, storage).fork_for_owner(
        owner_id,
        source.id,
        through_sequence_number=1,
        replacement_content="edited and resent",
    )

    copied_messages = [
        item
        for item in session.add_all.call_args.args[0]
        if isinstance(item, Message)
    ]
    assert [item.content for item in copied_messages] == ["edited and resent"]
    assert messages[0].content == "original prompt"
    session.commit.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_fork_rejects_assistant_replacement_before_storage_copy(tmp_path):
    storage = LocalAssetStorage(tmp_path / "assets")
    owner_id = uuid4()
    source, messages, _source_asset, _content = _source_snapshot(storage, owner_id)
    session = AsyncMock(spec=AsyncSession)
    session.execute.side_effect = [
        _result(scalar=source),
        _result(rows=messages),
    ]

    with pytest.raises(ConversationForkInvalidError):
        await ConversationForkService(session, storage).fork_for_owner(
            owner_id,
            source.id,
            through_sequence_number=2,
            replacement_content="not allowed",
        )

    session.add_all.assert_not_called()
    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once_with()
    assert tuple(storage.staging_root.iterdir()) == ()


@pytest.mark.asyncio
async def test_fork_missing_and_foreign_sources_fail_identically(tmp_path):
    storage = LocalAssetStorage(tmp_path / "assets")
    for _case in ("missing", "foreign"):
        session = AsyncMock(spec=AsyncSession)
        session.execute.return_value = _result(scalar=None)

        with pytest.raises(ConversationForkNotFoundError) as caught:
            await ConversationForkService(session, storage).fork_for_owner(
                uuid4(),
                uuid4(),
            )

        assert str(caught.value) == (
            "conversation is not available to the current user"
        )
        session.commit.assert_not_awaited()
        session.rollback.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_fork_rejects_nonexistent_branch_point(tmp_path):
    storage = LocalAssetStorage(tmp_path / "assets")
    owner_id = uuid4()
    source, messages, _source_asset, _content = _source_snapshot(storage, owner_id)
    session = AsyncMock(spec=AsyncSession)
    session.execute.side_effect = [
        _result(scalar=source),
        _result(rows=(messages[0],)),
    ]

    with pytest.raises(ConversationForkInvalidError):
        await ConversationForkService(session, storage).fork_for_owner(
            owner_id,
            source.id,
            through_sequence_number=9,
        )

    session.add_all.assert_not_called()
    session.rollback.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_fork_bounds_total_content_before_copy(tmp_path):
    storage = LocalAssetStorage(tmp_path / "assets")
    owner_id = uuid4()
    source = Conversation(
        id=uuid4(),
        owner_id=owner_id,
        title=None,
        next_message_sequence=2,
        is_pinned=False,
        is_archived=False,
    )
    message = Message(
        id=uuid4(),
        conversation_id=source.id,
        role=MessageRole.USER,
        content="x" * (MAX_CONVERSATION_FORK_CONTENT_CHARACTERS + 1),
        sequence_number=1,
    )
    message.asset_links = []
    message.citation_links = []
    session = AsyncMock(spec=AsyncSession)
    session.execute.side_effect = [
        _result(scalar=source),
        _result(rows=(message,)),
    ]

    with pytest.raises(ConversationForkTooLargeError):
        await ConversationForkService(session, storage).fork_for_owner(
            owner_id,
            source.id,
        )

    session.add_all.assert_not_called()
    session.rollback.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_fork_integrity_mismatch_leaves_no_new_private_object(tmp_path):
    storage = LocalAssetStorage(tmp_path / "assets")
    owner_id = uuid4()
    source, messages, source_asset, _content = _source_snapshot(storage, owner_id)
    source_asset.content_sha256 = "0" * 64
    session = AsyncMock(spec=AsyncSession)
    session.execute.side_effect = [
        _result(scalar=source),
        _result(rows=messages),
    ]
    original_paths = tuple(storage.objects_root.rglob("*"))

    with pytest.raises(ConversationForkStorageError):
        await ConversationForkService(session, storage).fork_for_owner(
            owner_id,
            source.id,
        )

    assert tuple(storage.objects_root.rglob("*")) == original_paths
    assert tuple(storage.staging_root.iterdir()) == ()
    session.add_all.assert_not_called()
    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_fork_commit_failure_compensates_finalized_asset(tmp_path):
    storage = LocalAssetStorage(tmp_path / "assets")
    owner_id = uuid4()
    source, messages, source_asset, _content = _source_snapshot(storage, owner_id)
    session = AsyncMock(spec=AsyncSession)
    session.execute.side_effect = [
        _result(scalar=source),
        _result(rows=messages),
    ]
    session.commit.side_effect = RuntimeError("database commit failed")

    with pytest.raises(RuntimeError, match="database commit failed"):
        await ConversationForkService(session, storage).fork_for_owner(
            owner_id,
            source.id,
        )

    copied_asset = next(
        item
        for item in session.add_all.call_args.args[0]
        if isinstance(item, Asset)
    )
    assert copied_asset.storage_key != source_asset.storage_key
    assert not storage.path_for(copied_asset.storage_key).exists()
    assert storage.path_for(source_asset.storage_key).exists()
    session.rollback.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_fork_remaps_generated_asset_provenance_within_copy(tmp_path):
    storage = LocalAssetStorage(tmp_path / "assets")
    owner_id = uuid4()
    source, messages, source_asset, _content = _source_snapshot(storage, owner_id)
    edited_id = uuid4()
    edited_content = b"private edited image"
    writer = storage.begin_write(edited_id)
    writer.write(edited_content)
    edited_key = writer.finalize()
    edited_asset = Asset(
        id=edited_id,
        owner_id=owner_id,
        original_filename="edited.png",
        media_type="image/png",
        byte_size=len(edited_content),
        content_sha256=hashlib.sha256(edited_content).hexdigest(),
        storage_key=edited_key,
        upload_idempotency_key=uuid4(),
        provenance_kind=AssetProvenanceKind.IMAGE_EDITING,
        source_asset_id=source_asset.id,
        runtime_id="comfyui",
        model_id=f"comfyui:{'a' * 24}",
    )
    messages[0].asset_links.append(
        MessageAsset(asset_id=edited_asset.id, asset=edited_asset, position=2)
    )
    session = AsyncMock(spec=AsyncSession)
    session.execute.side_effect = [
        _result(scalar=source),
        _result(rows=messages),
    ]

    await ConversationForkService(session, storage).fork_for_owner(
        owner_id,
        source.id,
    )

    copied_assets = {
        item.original_filename: item
        for item in session.add_all.call_args.args[0]
        if isinstance(item, Asset)
    }
    assert copied_assets["edited.png"].source_asset_id == copied_assets[
        "owner.png"
    ].id
    assert copied_assets["edited.png"].source_asset_id != source_asset.id
    session.commit.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_fork_bounds_declared_active_media_before_read(tmp_path):
    storage = LocalAssetStorage(tmp_path / "assets")
    owner_id = uuid4()
    source, messages, source_asset, _content = _source_snapshot(storage, owner_id)
    source_asset.byte_size = MAX_CONVERSATION_FORK_ASSET_BYTES + 1
    session = AsyncMock(spec=AsyncSession)
    session.execute.side_effect = [
        _result(scalar=source),
        _result(rows=messages),
    ]

    with pytest.raises(ConversationForkTooLargeError):
        await ConversationForkService(session, storage).fork_for_owner(
            owner_id,
            source.id,
        )

    session.add_all.assert_not_called()
    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_fork_rejects_blank_replacement_before_database_work(tmp_path):
    storage = LocalAssetStorage(tmp_path / "assets")
    session = AsyncMock(spec=AsyncSession)

    with pytest.raises(ValueError, match="must not be blank"):
        await ConversationForkService(session, storage).fork_for_owner(
            uuid4(),
            uuid4(),
            through_sequence_number=1,
            replacement_content="   ",
        )

    session.execute.assert_not_awaited()
