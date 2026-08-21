from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.conversation import Conversation
    from app.models.message_asset import MessageAsset


MAX_MESSAGE_CONTENT_CHARACTERS = 100_000


class MessageContentTooLargeError(ValueError):
    """A Message cannot be persisted within the fixed content bound."""


def validate_message_content(content: str) -> None:
    if not isinstance(content, str):
        raise TypeError("value must be a string")
    if len(content) > MAX_MESSAGE_CONTENT_CHARACTERS:
        raise MessageContentTooLargeError("persisted text is too large")


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(
            "role IN ('system', 'user', 'assistant', 'tool')",
            name="role_allowed",
        ),
        CheckConstraint(
            "sequence_number >= 1",
            name="sequence_number_positive",
        ),
        CheckConstraint(
            f"char_length(content) <= {MAX_MESSAGE_CONTENT_CHARACTERS}",
            name="content_length_bounded",
        ),
        UniqueConstraint(
            "conversation_id",
            "sequence_number",
            name="uq_messages_conversation_sequence_number",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[MessageRole] = mapped_column(
        Enum(
            MessageRole,
            name="message_role",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=lambda enum_type: [member.value for member in enum_type],
            length=32,
        ),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sequence_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    conversation: Mapped[Conversation] = relationship(
        back_populates="messages",
        lazy="raise",
    )
    asset_links: Mapped[list[MessageAsset]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="MessageAsset.position",
        passive_deletes=True,
    )
