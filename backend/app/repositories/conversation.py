from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Integer, bindparam, delete, func, select, tuple_, update

from app.models.asset import Asset
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.message_asset import MessageAsset
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
    include_archived: bool = False

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
        if not isinstance(self.include_archived, bool):
            raise TypeError("include_archived must be a boolean")


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
        if not pagination.include_archived:
            statement = statement.where(Conversation.is_archived.is_(False))
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

    async def set_state_for_owner(
        self,
        owner_id: UUID,
        conversation_id: UUID,
        *,
        is_pinned: bool | None = None,
        is_archived: bool | None = None,
    ) -> Conversation | None:
        values: dict[str, bool] = {}
        if is_pinned is not None:
            if not isinstance(is_pinned, bool):
                raise TypeError("is_pinned must be a boolean")
            values["is_pinned"] = is_pinned
        if is_archived is not None:
            if not isinstance(is_archived, bool):
                raise TypeError("is_archived must be a boolean")
            values["is_archived"] = is_archived
        if not values:
            raise ValueError("at least one conversation state field is required")
        statement = (
            update(Conversation)
            .where(
                Conversation.owner_id == owner_id,
                Conversation.id == conversation_id,
            )
            .values(**values)
            .returning(Conversation)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def soft_delete_assets_for_owner_conversation(
        self,
        owner_id: UUID,
        conversation_id: UUID,
    ) -> tuple[str, ...]:
        asset_ids = (
            select(MessageAsset.asset_id)
            .join(Message, Message.id == MessageAsset.message_id)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Message.conversation_id == conversation_id,
                Conversation.id == conversation_id,
                Conversation.owner_id == owner_id,
            )
        )
        statement = (
            update(Asset)
            .where(Asset.id.in_(asset_ids), Asset.deleted_at.is_(None))
            .values(deleted_at=func.now())
            .returning(Asset.storage_key)
        )
        result = await self.session.execute(statement)
        return tuple(result.scalars().all())

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
