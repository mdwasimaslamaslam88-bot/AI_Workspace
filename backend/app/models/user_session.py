from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.user import User


MAX_USER_SESSION_LABEL_CHARACTERS = 80


class UserSession(Base):
    __tablename__ = "user_sessions"
    __table_args__ = (
        CheckConstraint(
            "access_token_digest ~ '^[0-9a-f]{64}$'",
            name="access_token_digest_lowercase_hex",
        ),
        CheckConstraint(
            "label IS NULL OR (char_length(trim(label)) BETWEEN 1 AND 80)",
            name="label_bounded_nonblank",
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="revoked_at_not_before_created_at",
        ),
        UniqueConstraint(
            "access_token_digest",
            name="uq_user_sessions_access_token_digest",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    access_token_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str | None] = mapped_column(
        String(MAX_USER_SESSION_LABEL_CHARACTERS),
        nullable=True,
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
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    user: Mapped[User] = relationship(
        back_populates="sessions",
        lazy="raise",
    )


Index(
    "ix_user_sessions_user_revoked_created_at_id",
    UserSession.user_id,
    UserSession.revoked_at,
    UserSession.created_at.desc(),
    UserSession.id.desc(),
)
