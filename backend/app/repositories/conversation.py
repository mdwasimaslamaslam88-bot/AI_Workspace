from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Integer, bindparam, delete, select, tuple_, update

from app.models.conversation import Conversation
from app.repositories.base import BaseRepository


DEFAULT_CONVERSATION_PAGE_SIZE = 50
MAX_CONVERSATION_PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class ConversationCursor:
    updated_at: datetime
    id: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.updated_at, datetime):
            raise TypeError("cursor updated_at must be a datetime")
        if self.updated_at.tzinfo is None or self.updated_at.utcoffset() is None:
            raise ValueError("cursor updated_at must be timezone-aware")
        if not isinstance(self.id, UUID):
            raise TypeError("cursor id must be a UUID")


@dataclass(frozen=True, slots=True)
class ConversationPagination:
    limit: int = DEFAULT_CONVERSATION_PAGE_SIZE
    cursor: ConversationCursor | None = None

    def __post_init__(self) -> None:
        if isinstance(self.limit, bool) or not isinstance(self.limit, int):
            raise TypeError("pagination limit must be an integer")
        if not 1 <= self.limit <= MAX_CONVERSATION_PAGE_SIZE:
            raise ValueError(
                f"pagination limit must be between 1 and {MAX_CONVERSATION_PAGE_SIZE}"
            )
        if self.cursor is not None and not isinstance(
            self.cursor, ConversationCursor
        ):
            raise TypeError("pagination cursor must be a ConversationCursor")


@dataclass(frozen=True, slots=True)
class ConversationPage:
    items: tuple[Conversation, ...]
    next_cursor: ConversationCursor | None


class ConversationRepository(BaseRepository):
    async def create(self, owner_id: UUID, title: str | None) -> Conversation:
        conversation = Conversation(owner_id=owner_id, title=title)
        self.session.add(conversation)
        await self.session.flush()
        return conversation

    async def get_for_owner(
        self,
        owner_id: UUID,
        conversation_id: UUID,
    ) -> Conversation | None:
        statement = select(Conversation).where(
            Conversation.owner_id == owner_id,
            Conversation.id == conversation_id,
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def list_for_owner(
        self,
        owner_id: UUID,
        pagination: ConversationPagination | None = None,
    ) -> ConversationPage:
        if pagination is None:
            pagination = ConversationPagination()
        elif not isinstance(pagination, ConversationPagination):
            raise TypeError("pagination must be a ConversationPagination")

        statement = select(Conversation).where(Conversation.owner_id == owner_id)
        if pagination.cursor is not None:
            statement = statement.where(
                tuple_(Conversation.updated_at, Conversation.id)
                < tuple_(
                    bindparam(
                        "cursor_updated_at",
                        pagination.cursor.updated_at,
                        type_=Conversation.updated_at.type,
                    ),
                    bindparam(
                        "cursor_id",
                        pagination.cursor.id,
                        type_=Conversation.id.type,
                    ),
                )
            )

        statement = statement.order_by(
            Conversation.updated_at.desc(),
            Conversation.id.desc(),
        ).limit(
            bindparam(
                "fetch_limit",
                pagination.limit + 1,
                type_=Integer(),
            )
        )

        result = await self.session.execute(statement)
        conversations = list(result.scalars().all())
        has_more = len(conversations) > pagination.limit
        items = tuple(conversations[: pagination.limit])
        next_cursor = None
        if has_more:
            last_item = items[-1]
            next_cursor = ConversationCursor(
                updated_at=last_item.updated_at,
                id=last_item.id,
            )

        return ConversationPage(items=items, next_cursor=next_cursor)

    async def rename_for_owner(
        self,
        owner_id: UUID,
        conversation_id: UUID,
        title: str | None,
    ) -> Conversation | None:
        statement = (
            update(Conversation)
            .where(
                Conversation.owner_id == owner_id,
                Conversation.id == conversation_id,
            )
            .values(title=title)
            .returning(Conversation)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def delete_for_owner(
        self,
        owner_id: UUID,
        conversation_id: UUID,
    ) -> bool:
        statement = (
            delete(Conversation)
            .where(
                Conversation.owner_id == owner_id,
                Conversation.id == conversation_id,
            )
            .returning(Conversation.id)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none() is not None
