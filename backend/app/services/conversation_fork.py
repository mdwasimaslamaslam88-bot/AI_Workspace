from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.core.logging import get_logger
from app.models.asset import Asset
from app.models.conversation import Conversation
from app.models.document import DocumentChunk
from app.models.message import Message, MessageRole, validate_message_content
from app.models.message_asset import MessageAsset
from app.models.message_citation import MessageCitation
from app.services.asset import ASSET_COPY_CHUNK_BYTES
from app.storage.base import AssetStorage, StagedAssetWrite


MAX_CONVERSATION_FORK_MESSAGES = 100
MAX_CONVERSATION_FORK_CONTENT_CHARACTERS = 100_000
MAX_CONVERSATION_FORK_ASSET_BYTES = 256 * 1024 * 1024

logger = get_logger(__name__)


class ConversationForkNotFoundError(RuntimeError):
    """The current owner cannot access the requested source conversation."""


class ConversationForkInvalidError(RuntimeError):
    """The requested immutable branch point is not valid for the source."""


class ConversationForkTooLargeError(RuntimeError):
    """The requested bounded fork exceeds a fixed product limit."""


class ConversationForkStorageError(RuntimeError):
    """A private source asset could not be copied safely."""


def _copy_title(title: str | None) -> str | None:
    if title is None:
        return None
    suffix = " (copy)"
    return f"{title[: 255 - len(suffix)]}{suffix}"


class ConversationForkService:
    def __init__(self, session: AsyncSession, storage: AssetStorage) -> None:
        self.session = session
        self.storage = storage

    async def fork_for_owner(
        self,
        owner_id: UUID,
        conversation_id: UUID,
        *,
        through_sequence_number: int | None = None,
        replacement_content: str | None = None,
    ) -> Conversation:
        if through_sequence_number is not None:
            if isinstance(through_sequence_number, bool) or not isinstance(
                through_sequence_number, int
            ):
                raise TypeError("through_sequence_number must be an integer")
            if through_sequence_number < 1:
                raise ValueError("through_sequence_number must be positive")
        if replacement_content is not None:
            validate_message_content(replacement_content)
            if replacement_content.strip() == "":
                raise ValueError("replacement_content must not be blank")
            if through_sequence_number is None:
                raise ValueError(
                    "replacement_content requires through_sequence_number"
                )

        finalized_assets: list[tuple[UUID, str]] = []
        try:
            source = await self._lock_source_for_owner(owner_id, conversation_id)
            if source is None:
                raise ConversationForkNotFoundError(
                    "conversation is not available to the current user"
                )
            messages = await self._load_messages(
                owner_id,
                source.id,
                through_sequence_number=through_sequence_number,
            )
            self._validate_snapshot(
                source,
                messages,
                through_sequence_number=through_sequence_number,
                replacement_content=replacement_content,
            )

            source_assets = self._source_assets(owner_id, messages)
            active_asset_bytes = sum(
                asset.byte_size
                for asset in source_assets.values()
                if asset.deleted_at is None
            )
            if active_asset_bytes > MAX_CONVERSATION_FORK_ASSET_BYTES:
                raise ConversationForkTooLargeError(
                    "conversation assets exceed the fork byte limit"
                )

            copied_storage: dict[UUID, tuple[UUID, str]] = {}
            for source_asset in source_assets.values():
                copied_id = uuid4()
                if source_asset.deleted_at is None:
                    storage_key = await self._copy_active_asset(
                        source_asset,
                        copied_id,
                    )
                    finalized_assets.append((copied_id, storage_key))
                else:
                    storage_key = self.storage.key_for(copied_id)
                copied_storage[source_asset.id] = (copied_id, storage_key)

            copied_assets = {
                source_id: Asset(
                    id=copied_id,
                    owner_id=owner_id,
                    original_filename=source_asset.original_filename,
                    media_type=source_asset.media_type,
                    byte_size=source_asset.byte_size,
                    content_sha256=source_asset.content_sha256,
                    storage_key=storage_key,
                    upload_idempotency_key=uuid4(),
                    deleted_at=source_asset.deleted_at,
                    provenance_kind=source_asset.provenance_kind,
                    source_asset_id=(
                        copied_storage[source_asset.source_asset_id][0]
                        if source_asset.source_asset_id in copied_storage
                        else source_asset.source_asset_id
                    ),
                    runtime_id=source_asset.runtime_id,
                    model_id=source_asset.model_id,
                )
                for source_id, source_asset in source_assets.items()
                for copied_id, storage_key in (copied_storage[source_id],)
            }

            fork = Conversation(
                id=uuid4(),
                owner_id=owner_id,
                title=_copy_title(source.title),
                next_message_sequence=len(messages) + 1,
                is_pinned=False,
                is_archived=False,
            )
            copied_messages: list[Message] = []
            for source_message in messages:
                content = (
                    replacement_content
                    if replacement_content is not None
                    and source_message.sequence_number
                    == through_sequence_number
                    else source_message.content
                )
                copied_message = Message(
                    id=uuid4(),
                    conversation_id=fork.id,
                    role=source_message.role,
                    content=content,
                    sequence_number=source_message.sequence_number,
                )
                copied_message.asset_links = [
                    MessageAsset(
                        asset=copied_assets[link.asset_id],
                        position=link.position,
                    )
                    for link in source_message.asset_links
                ]
                copied_message.citation_links = [
                    MessageCitation(
                        document_chunk_id=link.document_chunk_id,
                        position=link.position,
                    )
                    for link in source_message.citation_links
                ]
                copied_messages.append(copied_message)

            self.session.add_all(
                [*copied_assets.values(), fork, *copied_messages]
            )
            await self.session.flush()
            await self.session.commit()
            return fork
        except BaseException:
            await self.session.rollback()
            await self._delete_copied_assets(finalized_assets)
            raise

    async def _lock_source_for_owner(
        self,
        owner_id: UUID,
        conversation_id: UUID,
    ) -> Conversation | None:
        result = await self.session.execute(
            select(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.owner_id == owner_id,
            )
            .with_for_update(of=Conversation)
        )
        return result.scalar_one_or_none()

    async def _load_messages(
        self,
        owner_id: UUID,
        conversation_id: UUID,
        *,
        through_sequence_number: int | None,
    ) -> tuple[Message, ...]:
        statement = (
            select(Message)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Conversation.owner_id == owner_id,
                Conversation.id == conversation_id,
                Message.conversation_id == conversation_id,
            )
            .options(
                selectinload(Message.asset_links).joinedload(MessageAsset.asset),
                selectinload(Message.citation_links)
                .joinedload(MessageCitation.chunk)
                .joinedload(DocumentChunk.asset),
            )
            .order_by(Message.sequence_number.asc())
            .limit(MAX_CONVERSATION_FORK_MESSAGES + 1)
        )
        if through_sequence_number is not None:
            statement = statement.where(
                Message.sequence_number <= through_sequence_number
            )
        result = await self.session.execute(statement)
        return tuple(result.scalars().unique().all())

    @staticmethod
    def _validate_snapshot(
        source: Conversation,
        messages: tuple[Message, ...],
        *,
        through_sequence_number: int | None,
        replacement_content: str | None,
    ) -> None:
        if len(messages) > MAX_CONVERSATION_FORK_MESSAGES:
            raise ConversationForkTooLargeError(
                "conversation exceeds the fork message limit"
            )
        if through_sequence_number is not None and (
            not messages
            or messages[-1].sequence_number != through_sequence_number
        ):
            raise ConversationForkInvalidError(
                "conversation branch point is unavailable"
            )
        expected_final = (
            through_sequence_number
            if through_sequence_number is not None
            else source.next_message_sequence - 1
        )
        if tuple(message.sequence_number for message in messages) != tuple(
            range(1, expected_final + 1)
        ):
            raise ConversationForkInvalidError(
                "conversation history is not contiguous"
            )
        if replacement_content is not None and (
            not messages or messages[-1].role is not MessageRole.USER
        ):
            raise ConversationForkInvalidError(
                "only a user branch message can be replaced"
            )
        content_characters = sum(
            len(
                replacement_content
                if replacement_content is not None
                and message.sequence_number == through_sequence_number
                else message.content
            )
            for message in messages
        )
        if content_characters > MAX_CONVERSATION_FORK_CONTENT_CHARACTERS:
            raise ConversationForkTooLargeError(
                "conversation exceeds the fork content limit"
            )

    @staticmethod
    def _source_assets(
        owner_id: UUID,
        messages: tuple[Message, ...],
    ) -> dict[UUID, Asset]:
        assets: dict[UUID, Asset] = {}
        for message in messages:
            for link in message.asset_links:
                if link.asset.owner_id != owner_id:
                    raise ConversationForkInvalidError(
                        "conversation attachment ownership is inconsistent"
                    )
                assets[link.asset_id] = link.asset
            for link in message.citation_links:
                if (
                    link.chunk.owner_id != owner_id
                    or link.chunk.asset.owner_id != owner_id
                ):
                    raise ConversationForkInvalidError(
                        "conversation citation ownership is inconsistent"
                    )
        return assets

    async def _copy_active_asset(self, source: Asset, copied_id: UUID) -> str:
        writer = await asyncio.to_thread(self.storage.begin_write, copied_id)
        finalized = False
        try:
            handle = await asyncio.to_thread(
                self.storage.open_read,
                source.storage_key,
            )
            try:
                while True:
                    chunk = await asyncio.to_thread(
                        handle.read,
                        ASSET_COPY_CHUNK_BYTES,
                    )
                    if not chunk:
                        break
                    await asyncio.to_thread(writer.write, chunk)
                    if writer.byte_size > source.byte_size:
                        raise ConversationForkStorageError(
                            "private source asset size is inconsistent"
                        )
            finally:
                await asyncio.to_thread(handle.close)
            if (
                writer.byte_size != source.byte_size
                or writer.content_sha256 != source.content_sha256
            ):
                raise ConversationForkStorageError(
                    "private source asset integrity check failed"
                )
            storage_key = await asyncio.to_thread(writer.finalize)
            finalized = True
            return storage_key
        except ConversationForkStorageError:
            raise
        except Exception as exc:
            raise ConversationForkStorageError(
                "private source asset could not be copied"
            ) from exc
        finally:
            if not finalized:
                await self._abort_writer(writer)

    @staticmethod
    async def _abort_writer(writer: StagedAssetWrite) -> None:
        try:
            await asyncio.to_thread(writer.abort)
        except (OSError, RuntimeError):
            logger.warning("conversation_fork_staging_cleanup_deferred")

    async def _delete_copied_assets(
        self,
        finalized_assets: list[tuple[UUID, str]],
    ) -> None:
        for asset_id, storage_key in finalized_assets:
            try:
                await asyncio.to_thread(self.storage.delete, storage_key)
            except (OSError, RuntimeError):
                logger.warning(
                    "conversation_fork_asset_cleanup_deferred",
                    asset_id=str(asset_id),
                )
