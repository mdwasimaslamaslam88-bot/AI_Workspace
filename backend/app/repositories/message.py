from uuid import UUID

from sqlalchemy import update

from app.models.conversation import Conversation
from app.models.message import Message, MessageRole
from app.repositories.base import BaseRepository


class MessageRepository(BaseRepository):
    async def append_for_owner(
        self,
        owner_id: UUID,
        conversation_id: UUID,
        role: MessageRole,
        content: str,
    ) -> Message | None:
        allocation = (
            update(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.owner_id == owner_id,
            )
            .values(
                next_message_sequence=Conversation.next_message_sequence + 1,
            )
            .returning(
                (Conversation.next_message_sequence - 1).label(
                    "allocated_sequence_number"
                )
            )
        )

        result = await self.session.execute(allocation)
        sequence_number = result.scalar_one_or_none()
        if sequence_number is None:
            return None

        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            sequence_number=sequence_number,
        )
        self.session.add(message)
        await self.session.flush()
        return message
