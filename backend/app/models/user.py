from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, ClassVar
from uuid import UUID, uuid4

from sqlalchemy import DateTime, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.conversation import Conversation
    from app.models.user_session import UserSession


class User(Base):
    __tablename__ = "users"

    _authenticated_session_id: ClassVar[UUID | None] = None
    _authenticated_session_digest: ClassVar[str | None] = None

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    access_token_digest: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="owner",
        lazy="raise",
        passive_deletes="all",
    )
    sessions: Mapped[list[UserSession]] = relationship(
        back_populates="user",
        lazy="raise",
        passive_deletes=True,
    )

    def bind_authenticated_session(
        self,
        session_id: UUID,
        access_token_digest: str,
    ) -> None:
        self._authenticated_session_id = session_id
        self._authenticated_session_digest = access_token_digest

    def clear_authenticated_session(self) -> None:
        self._authenticated_session_id = None
        self._authenticated_session_digest = None

    @property
    def authenticated_session_id(self) -> UUID | None:
        return self._authenticated_session_id

    @property
    def authenticated_session_digest(self) -> str | None:
        return self._authenticated_session_digest
