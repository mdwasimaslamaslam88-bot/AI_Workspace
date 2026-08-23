import asyncio
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole, validate_message_content
from app.repositories.conversation import (
    ConversationPage,
    ConversationPagination,
    ConversationRepository,
)
from app.repositories.message import (
    MessageAttachmentClaimError,
    MessageRepository,
)
from app.services.message import MessageAttachmentUnavailableError
from app.storage.base import AssetStorage
from app.storage.local import StorageError

logger = get_logger(__name__)


class ConversationService:
    def __init__(self, session: AsyncSession, storage: AssetStorage | None = None) -> None:
        self.session = session
        self.storage = storage
        self.repository = ConversationRepository(session)
        self.message_repository = MessageRepository(session)

    async def create(self, owner_id: UUID, title: str | None) -> Conversation:
        try:
            conversation = await self.repository.create(owner_id, title)
            await self.session.commit()
            return conversation
        except BaseException:
            await self.session.rollback()
            raise

    async def create_with_initial_message_for_owner(
        self,
        owner_id: UUID,
        title: str | None,
        role: MessageRole,
        content: str,
        *,
        system_prompt: str | None = None,
        attachment_ids: tuple[UUID, ...] = (),
    ) -> tuple[Conversation, Message] | None:
        if system_prompt is not None:
            validate_message_content(system_prompt)
        validate_message_content(content)
        try:
            conversation = await self.repository.create(owner_id, title)
            if system_prompt is not None:
                system_message = await self.message_repository.append_for_owner(
                    owner_id,
                    conversation.id,
                    MessageRole.SYSTEM,
                    system_prompt,
                )
                if system_message is None:
                    await self.session.rollback()
                    return None

            message = await self.message_repository.append_for_owner(
                owner_id,
                conversation.id,
                role,
                content,
                **({"attachment_ids": attachment_ids} if attachment_ids else {}),
            )
            if message is None:
                await self.session.rollback()
                return None

            await self.session.commit()
            return conversation, message
        except MessageAttachmentClaimError as exc:
            await self.session.rollback()
            raise MessageAttachmentUnavailableError(
                "one or more attachments are unavailable"
            ) from exc
        except BaseException:
            await self.session.rollback()
            raise

    async def get_for_owner(
        self,
        owner_id: UUID,
        conversation_id: UUID,
    ) -> Conversation | None:
        try:
            return await self.repository.get_for_owner(owner_id, conversation_id)
        except BaseException:
            await self.session.rollback()
            raise

    async def list_for_owner(
        self,
        owner_id: UUID,
        pagination: ConversationPagination | None = None,
    ) -> ConversationPage:
        try:
            return await self.repository.list_for_owner(owner_id, pagination)
        except BaseException:
            await self.session.rollback()
            raise

    async def rename_for_owner(
        self,
        owner_id: UUID,
        conversation_id: UUID,
        title: str | None,
    ) -> Conversation | None:
        try:
            conversation = await self.repository.rename_for_owner(
                owner_id,
                conversation_id,
                title,
            )
            if conversation is None:
                await self.session.rollback()
                return None

            await self.session.commit()
            return conversation
        except BaseException:
            await self.session.rollback()
            raise

    async def set_state_for_owner(
        self,
        owner_id: UUID,
        conversation_id: UUID,
        *,
        is_pinned: bool | None = None,
        is_archived: bool | None = None,
    ) -> Conversation | None:
        try:
            conversation = await self.repository.set_state_for_owner(
                owner_id,
                conversation_id,
                is_pinned=is_pinned,
                is_archived=is_archived,
            )
            if conversation is None:
                await self.session.rollback()
                return None
            await self.session.commit()
            return conversation
        except BaseException:
            await self.session.rollback()
            raise

    async def delete_for_owner(
        self,
        owner_id: UUID,
        conversation_id: UUID,
    ) -> bool:
        try:
            storage_keys = (
                await self.repository.soft_delete_assets_for_owner_conversation(
                    owner_id, conversation_id
                )
            )
            deleted = await self.repository.delete_for_owner(
                owner_id,
                conversation_id,
            )
            if not deleted:
                await self.session.rollback()
                return False

            await self.session.commit()
            if self.storage is not None:
                for storage_key in storage_keys:
                    try:
                        await asyncio.to_thread(self.storage.delete, storage_key)
                    except (StorageError, OSError):
                        logger.warning(
                            "conversation_asset_delete_deferred",
                            conversation_id=str(conversation_id),
                        )
            return True
        except BaseException:
            await self.session.rollback()
            raise
