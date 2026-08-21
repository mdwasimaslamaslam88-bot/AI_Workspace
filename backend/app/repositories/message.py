from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Integer, bindparam, func, or_, select, true, update

from app.models.asset import Asset
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole, validate_message_content
from app.models.message_asset import MessageAsset
from app.repositories.base import BaseRepository


DEFAULT_MESSAGE_PAGE_SIZE = 50
MAX_MESSAGE_PAGE_SIZE = 100
MAX_MESSAGE_PAGE_CONTENT_CHARACTERS = 100_000


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


@dataclass(frozen=True, slots=True)
class GenerationContextMessage:
    role: MessageRole
    content: str
    sequence_number: int


@dataclass(frozen=True, slots=True)
class GenerationContextSnapshot:
    messages: tuple[GenerationContextMessage, ...]
    candidate_count: int
    final_sequence_number: int | None
    oversized: bool


class MessageAttachmentClaimError(RuntimeError):
    """An attachment cannot be claimed by this owner's new message."""


class MessageRepository(BaseRepository):
    async def append_for_owner(
        self,
        owner_id: UUID,
        conversation_id: UUID,
        role: MessageRole,
        content: str,
        *,
        expected_sequence_number: int | None = None,
        attachment_ids: tuple[UUID, ...] = (),
    ) -> Message | None:
        validate_message_content(content)
        if len(attachment_ids) != len(set(attachment_ids)):
            raise ValueError("attachment_ids must be unique")
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

        assets_by_id: dict[UUID, Asset] = {}
        if attachment_ids:
            assets_statement = (
                select(Asset)
                .outerjoin(MessageAsset, MessageAsset.asset_id == Asset.id)
                .where(
                    Asset.id.in_(attachment_ids),
                    Asset.owner_id == owner_id,
                    Asset.deleted_at.is_(None),
                    MessageAsset.asset_id.is_(None),
                )
                .with_for_update(of=Asset)
            )
            assets_result = await self.session.execute(assets_statement)
            assets_by_id = {asset.id: asset for asset in assets_result.scalars()}
            if len(assets_by_id) != len(attachment_ids):
                raise MessageAttachmentClaimError(
                    "one or more attachments are unavailable"
                )

        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            sequence_number=sequence_number,
        )
        message.asset_links = [
            MessageAsset(asset=assets_by_id[asset_id], position=position)
            for position, asset_id in enumerate(attachment_ids, start=1)
        ]
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

        candidates = (
            select(
                Message.id.label("message_id"),
                Message.sequence_number.label("sequence_number"),
                func.char_length(Message.content).label(
                    "content_characters"
                ),
            )
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
            candidates = candidates.where(
                Message.sequence_number
                > bindparam(
                    "message_cursor_sequence_number",
                    pagination.cursor.sequence_number,
                    type_=Message.sequence_number.type,
                )
            )

        candidates = (
            candidates.order_by(Message.sequence_number.asc())
            .limit(
                bindparam(
                    "message_candidate_limit",
                    pagination.limit + 1,
                    type_=Integer(),
                )
            )
            .cte("message_page_candidates")
        )
        ranked_candidates = select(
            candidates.c.message_id,
            candidates.c.sequence_number,
            func.row_number()
            .over(order_by=candidates.c.sequence_number.asc())
            .label("candidate_position"),
            func.sum(candidates.c.content_characters)
            .over(
                order_by=candidates.c.sequence_number.asc(),
                rows=(None, 0),
            )
            .label("cumulative_content_characters"),
            func.count().over().label("candidate_count"),
        ).cte("ranked_message_page_candidates")
        selected_candidates = (
            select(
                ranked_candidates.c.message_id,
                ranked_candidates.c.sequence_number,
                ranked_candidates.c.candidate_count,
            )
            .where(
                ranked_candidates.c.candidate_position
                <= bindparam(
                    "message_page_limit",
                    pagination.limit,
                    type_=Integer(),
                ),
                ranked_candidates.c.cumulative_content_characters
                <= bindparam(
                    "message_page_content_character_limit",
                    MAX_MESSAGE_PAGE_CONTENT_CHARACTERS,
                    type_=Integer(),
                ),
            )
            .cte("selected_message_page_candidates")
        )
        statement = (
            select(Message, selected_candidates.c.candidate_count)
            .join(
                selected_candidates,
                selected_candidates.c.message_id == Message.id,
            )
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
        )

        result = await self.session.execute(statement)
        rows = result.all()
        items = tuple(row[0] for row in rows)
        candidate_count = rows[0][1] if rows else 0
        has_more = candidate_count > len(items)
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
        max_context_characters: int,
    ) -> GenerationContextSnapshot:
        if isinstance(max_messages, bool) or not isinstance(max_messages, int):
            raise TypeError("max_messages must be an integer")
        if max_messages < 1:
            raise ValueError("max_messages must be positive")
        if isinstance(max_context_characters, bool) or not isinstance(
            max_context_characters,
            int,
        ):
            raise TypeError("max_context_characters must be an integer")
        if max_context_characters < 1:
            raise ValueError("max_context_characters must be positive")

        candidates = (
            select(
                Message.id.label("message_id"),
                Message.sequence_number.label("sequence_number"),
                func.char_length(Message.content).label(
                    "content_characters"
                ),
            )
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
            .cte("generation_context_candidates")
        )
        metadata = (
            select(
                func.count().label("candidate_count"),
                func.coalesce(
                    func.sum(candidates.c.content_characters),
                    0,
                ).label("cumulative_content_characters"),
                func.max(candidates.c.sequence_number).label(
                    "final_candidate_sequence_number"
                ),
            )
            .select_from(candidates)
            .cte("generation_context_metadata")
        )
        eligible_candidates = (
            select(
                candidates.c.message_id,
                candidates.c.sequence_number,
            )
            .select_from(candidates)
            .join(metadata, true())
            .where(
                metadata.c.candidate_count
                <= bindparam(
                    "generation_context_message_limit",
                    max_messages,
                    type_=Integer(),
                ),
                metadata.c.cumulative_content_characters
                <= bindparam(
                    "generation_context_character_limit",
                    max_context_characters,
                    type_=Integer(),
                ),
            )
            .cte("eligible_generation_context_candidates")
        )
        projected_messages = (
            select(
                Message.role.label("message_role"),
                Message.content.label("message_content"),
                Message.sequence_number.label("message_sequence_number"),
            )
            .select_from(eligible_candidates)
            .join(
                Message,
                Message.id == eligible_candidates.c.message_id,
            )
            .join(
                Conversation,
                Conversation.id == Message.conversation_id,
            )
            .where(
                Conversation.owner_id == owner_id,
                Conversation.id == conversation_id,
                Message.conversation_id == conversation_id,
            )
            .cte("projected_generation_context_messages")
        )
        oversized = or_(
            metadata.c.candidate_count
            > bindparam(
                "generation_context_message_limit",
                max_messages,
                type_=Integer(),
            ),
            metadata.c.cumulative_content_characters
            > bindparam(
                "generation_context_character_limit",
                max_context_characters,
                type_=Integer(),
            ),
        ).label("generation_context_oversized")
        statement = (
            select(
                projected_messages.c.message_role,
                projected_messages.c.message_content,
                projected_messages.c.message_sequence_number,
                metadata.c.candidate_count,
                metadata.c.final_candidate_sequence_number,
                oversized,
            )
            .select_from(metadata)
            .outerjoin(projected_messages, true())
            .order_by(projected_messages.c.message_sequence_number.asc())
        )

        result = await self.session.execute(statement)
        rows = result.all()
        first_row = rows[0]
        messages = tuple(
            GenerationContextMessage(
                role=row.message_role,
                content=row.message_content,
                sequence_number=row.message_sequence_number,
            )
            for row in rows
            if row.message_sequence_number is not None
        )
        return GenerationContextSnapshot(
            messages=messages,
            candidate_count=first_row.candidate_count,
            final_sequence_number=(
                first_row.final_candidate_sequence_number
            ),
            oversized=first_row.generation_context_oversized,
        )
