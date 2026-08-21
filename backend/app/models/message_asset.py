from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Integer, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.asset import Asset
    from app.models.message import Message


class MessageAsset(Base):
    __tablename__ = "message_assets"
    __table_args__ = (
        CheckConstraint("position >= 1", name="position_positive"),
        UniqueConstraint(
            "message_id",
            "position",
            name="uq_message_assets_message_position",
        ),
        UniqueConstraint("asset_id", name="uq_message_assets_asset_id"),
    )

    message_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE"),
        primary_key=True,
    )
    asset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("assets.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    message: Mapped[Message] = relationship(
        back_populates="asset_links",
        lazy="raise",
    )
    asset: Mapped[Asset] = relationship(
        back_populates="message_link",
        lazy="joined",
    )

    @property
    def state(self) -> str:
        return "deleted" if self.asset.deleted_at is not None else "active"

    @property
    def original_filename(self) -> str | None:
        return None if self.asset.deleted_at is not None else self.asset.original_filename

    @property
    def media_type(self) -> str | None:
        return None if self.asset.deleted_at is not None else self.asset.media_type

    @property
    def byte_size(self) -> int | None:
        return None if self.asset.deleted_at is not None else self.asset.byte_size
