from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Integer, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.document import DocumentChunk
    from app.models.message import Message


class MessageCitation(Base):
    __tablename__ = "message_citations"
    __table_args__ = (
        CheckConstraint("position >= 1", name="position_positive"),
        UniqueConstraint(
            "message_id",
            "position",
            name="uq_message_citations_message_position",
        ),
    )

    message_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE"),
        primary_key=True,
    )
    document_chunk_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("document_chunks.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    message: Mapped[Message] = relationship(back_populates="citation_links", lazy="raise")
    chunk: Mapped[DocumentChunk] = relationship(
        back_populates="citation_links",
        lazy="joined",
    )

    @property
    def asset_id(self) -> UUID:
        return self.chunk.asset_id

    @property
    def state(self) -> str:
        return "deleted" if self.chunk.asset.deleted_at is not None else "active"

    @property
    def original_filename(self) -> str | None:
        if self.chunk.asset.deleted_at is not None:
            return None
        return self.chunk.asset.original_filename

    @property
    def page_number(self) -> int | None:
        return self.chunk.page_number

    @property
    def row_start(self) -> int | None:
        return self.chunk.row_start

    @property
    def row_end(self) -> int | None:
        return self.chunk.row_end

    @property
    def section(self) -> str | None:
        return self.chunk.section

    @property
    def excerpt(self) -> str | None:
        if self.chunk.asset.deleted_at is not None:
            return None
        return self.chunk.content[:400]
