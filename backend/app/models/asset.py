from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.message_asset import MessageAsset
    from app.models.user import User


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (
        CheckConstraint("byte_size > 0", name="byte_size_positive"),
        CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="content_sha256_lowercase_hex",
        ),
        CheckConstraint(
            "storage_key ~ '^objects/[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{32}$'",
            name="storage_key_generated",
        ),
        CheckConstraint(
            "deleted_at IS NULL OR deleted_at >= created_at",
            name="deleted_at_not_before_created_at",
        ),
        UniqueConstraint(
            "owner_id",
            "upload_idempotency_key",
            name="uq_assets_owner_upload_idempotency_key",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    original_filename: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    upload_idempotency_key: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    owner: Mapped[User] = relationship(lazy="raise")
    message_link: Mapped[MessageAsset | None] = relationship(
        back_populates="asset",
        lazy="raise",
        uselist=False,
        passive_deletes="all",
    )


Index(
    "ix_assets_owner_created_at_id",
    Asset.owner_id,
    Asset.created_at.desc(),
    Asset.id.desc(),
)
Index(
    "ix_assets_deleted_at",
    Asset.deleted_at,
    postgresql_where=text("deleted_at IS NOT NULL"),
)
