from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Integer, bindparam, select, update

from app.models.conversation import Conversation
from app.models.message import Message, MessageRole, validate_message_content
from app.repositories.base import BaseRepository


DEFAULT_MESSAGE_PAGE_SIZE = 50
MAX_MESSAGE_PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class MessageCursor:
    sequence_number: int

    def __post_init__(self) -> None:
        if isinstance(self.sequence_number, bool) or not isinstance(
            self.sequence_number,
            int,
        ):
            raise TypeError("message cursor sequence_number must be an integer")
        if self.sequence_number < 1:
            raise ValueError("message cursor sequence_number must be positive")


@dataclass(frozen=True, slots=True)
class MessagePagination:
    limit: int = DEFAULT_MESSAGE_PAGE_SIZE
    cursor: MessageCursor | None = None

    def __post_init__(self) -> None:
        if isinstance(self.limit, bool) or not isinstance(self.limit, int):
            raise TypeError("message pagination limit must be an integer")
        if not 1 <= self.limit <= MAX_MESSAGE_PAGE_SIZE:
            raise ValueError(
                f"message pagination limit must be between 1 and {MAX_MESSAGE_PAGE_SIZE}"
            )
        if self.cursor is not None and not isinstance(self.cursor, MessageCursor):
            raise TypeError("message pagination cursor must be a MessageCursor")


@dataclass(frozen=True, slots=True)
class MessagePage:
    items: tuple[Message, ...]
    next_cursor: MessageCursor | None


class MessageRepository(BaseRepository):
    async def append_for_owner(
        self,
        owner_id: UUID,
        conversation_id: UUID,
        role: MessageRole,
        content: str,
        *,
        expected_sequence_number: int | None = None,
    ) -> Message | None:
        validate_message_content(content)
        if expected_sequence_number is not None:
            if isinstance(expected_sequence_number, bool) or not isinstance(
                expected_sequence_number, int
            ):
                raise TypeError("expected_sequence_number must be an integer")
            if expected_sequence_number < 1:
                raise ValueError("expected_sequence_number must be positive")

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
        if expected_sequence_number is not None:
            allocation = allocation.where(
                Conversation.next_message_sequence == expected_sequence_number
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

    async def list_for_owner(
        self,
        owner_id: UUID,
        conversation_id: UUID,
        pagination: MessagePagination | None = None,
    ) -> MessagePage:
        if pagination is None:
            pagination = MessagePagination()
        elif not isinstance(pagination, MessagePagination):
            raise TypeError("pagination must be a MessagePagination")

        statement = (
            select(Message)
            .join(
                Conversation,
                Conversation.id == Message.conversation_id,
            )
            .where(
                Conversation.owner_id == owner_id,
                Conversation.id == conversation_id,
                Message.conversation_id == conversation_id,
            )
        )
        if pagination.cursor is not None:
            statement = statement.where(
                Message.sequence_number
                > bindparam(
                    "message_cursor_sequence_number",
                    pagination.cursor.sequence_number,
                    type_=Message.sequence_number.type,
                )
            )

        statement = statement.order_by(Message.sequence_number.asc()).limit(
            bindparam(
                "message_fetch_limit",
                pagination.limit + 1,
                type_=Integer(),
            )
        )

        result = await self.session.execute(statement)
        messages = list(result.scalars().all())
        has_more = len(messages) > pagination.limit
        items = tuple(messages[: pagination.limit])
        next_cursor = None
        if has_more:
            next_cursor = MessageCursor(
                sequence_number=items[-1].sequence_number,
            )

        return MessagePage(items=items, next_cursor=next_cursor)

    async def list_generation_context_for_owner(
        self,
        owner_id: UUID,
        conversation_id: UUID,
        *,
        max_messages: int,
    ) -> tuple[Message, ...]:
        if isinstance(max_messages, bool) or not isinstance(max_messages, int):
            raise TypeError("max_messages must be an integer")
        if max_messages < 1:
            raise ValueError("max_messages must be positive")

        statement = (
            select(Message)
            .join(
                Conversation,
                Conversation.id == Message.conversation_id,
            )
            .where(
                Conversation.owner_id == owner_id,
                Conversation.id == conversation_id,
                Message.conversation_id == conversation_id,
            )
            .order_by(Message.sequence_number.asc())
            .limit(
                bindparam(
                    "generation_context_fetch_limit",
                    max_messages + 1,
                    type_=Integer(),
                )
            )
        )

        result = await self.session.execute(statement)
        return tuple(result.scalars().all())
