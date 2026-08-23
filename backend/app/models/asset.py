from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
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


class AssetProvenanceKind(StrEnum):
    UPLOAD = "upload"
    IMAGE_GENERATION = "image_generation"
    IMAGE_EDITING = "image_editing"
    SPEECH_SYNTHESIS = "speech_synthesis"


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
        CheckConstraint(
            "provenance_kind IN ('upload', 'image_generation', "
            "'image_editing', 'speech_synthesis')",
            name="provenance_kind_known",
        ),
        CheckConstraint(
            "runtime_id IS NULL OR runtime_id ~ '^[a-z0-9][a-z0-9_-]{0,63}$'",
            name="runtime_id_safe",
        ),
        CheckConstraint(
            "model_id IS NULL OR model_id ~ "
            "'^[a-z0-9][a-z0-9_-]{0,63}:[a-f0-9]{24}$'",
            name="model_id_public",
        ),
        CheckConstraint(
            "(provenance_kind = 'upload' AND source_asset_id IS NULL "
            "AND runtime_id IS NULL AND model_id IS NULL) OR "
            "(provenance_kind IN ('image_generation', 'speech_synthesis') "
            "AND source_asset_id IS NULL AND runtime_id IS NOT NULL "
            "AND model_id IS NOT NULL) OR "
            "(provenance_kind = 'image_editing' AND source_asset_id IS NOT NULL "
            "AND runtime_id IS NOT NULL AND model_id IS NOT NULL)",
            name="provenance_consistent",
        ),
        CheckConstraint(
            "source_asset_id IS NULL OR source_asset_id <> id",
            name="source_not_self",
        ),
        ForeignKeyConstraint(
            ["source_asset_id", "owner_id"],
            ["assets.id", "assets.owner_id"],
            name="fk_assets_source_asset_id_assets",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "id",
            "owner_id",
            name="uq_assets_id_owner_id",
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
    provenance_kind: Mapped[AssetProvenanceKind] = mapped_column(
        String(32),
        nullable=False,
        default=AssetProvenanceKind.UPLOAD,
    )
    source_asset_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
    )
    runtime_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_id: Mapped[str | None] = mapped_column(String(96), nullable=True)

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
Index(
    "ix_assets_source_asset_id",
    Asset.source_asset_id,
    postgresql_where=text("source_asset_id IS NOT NULL"),
)
