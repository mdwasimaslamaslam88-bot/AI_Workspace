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
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.asset import Asset
    from app.models.message_citation import MessageCitation
    from app.models.user import User


DOCUMENT_EMBEDDING_DIMENSIONS = 256
DOCUMENT_EMBEDDING_BYTES = DOCUMENT_EMBEDDING_DIMENSIONS * 4
MAX_DOCUMENT_EMBEDDING_DIMENSIONS = 4_096
MAX_DOCUMENT_CHUNK_CHARACTERS = 1_600


class DocumentStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("chunk_count >= 0", name="chunk_count_nonnegative"),
        CheckConstraint(
            "character_count >= 0",
            name="character_count_nonnegative",
        ),
        CheckConstraint(
            "failure_code IS NULL OR failure_code ~ '^[a-z0-9_]{1,64}$'",
            name="failure_code_safe",
        ),
        CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'failed', 'cancelled')",
            name="status_allowed",
        ),
        CheckConstraint(
            "(status = 'processing') = (ingestion_token IS NOT NULL)",
            name="processing_token_consistent",
        ),
        UniqueConstraint("asset_id", name="uq_documents_asset_id"),
        UniqueConstraint("id", "owner_id", name="uq_documents_id_owner"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    asset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("assets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(
            DocumentStatus,
            name="document_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=lambda enum_type: [member.value for member in enum_type],
            length=32,
        ),
        nullable=False,
        default=DocumentStatus.PENDING,
    )
    ingestion_token: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    character_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    failure_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    owner: Mapped[User] = relationship(lazy="raise")
    asset: Mapped[Asset] = relationship(lazy="joined")
    chunks: Mapped[list[DocumentChunk]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
    )


Index("ix_documents_owner_status", Document.owner_id, Document.status)
Index("ix_documents_owner_updated_at", Document.owner_id, Document.updated_at.desc())


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        CheckConstraint("ordinal >= 1", name="ordinal_positive"),
        CheckConstraint(
            f"char_length(content) BETWEEN 1 AND {MAX_DOCUMENT_CHUNK_CHARACTERS}",
            name="content_length_bounded",
        ),
        CheckConstraint(
            "embedding_dimensions BETWEEN 1 AND "
            f"{MAX_DOCUMENT_EMBEDDING_DIMENSIONS}",
            name="embedding_dimensions_bounded",
        ),
        CheckConstraint(
            "octet_length(embedding) = embedding_dimensions * 4",
            name="embedding_bytes_consistent",
        ),
        CheckConstraint("embedding_norm > 0", name="embedding_norm_positive"),
        CheckConstraint(
            "embedding_model ~ '^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,254}$'",
            name="embedding_model_safe",
        ),
        CheckConstraint(
            "page_number IS NULL OR page_number >= 1",
            name="page_number_positive",
        ),
        CheckConstraint("row_start IS NULL OR row_start >= 1", name="row_start_positive"),
        CheckConstraint("row_end IS NULL OR row_end >= row_start", name="row_range_valid"),
        UniqueConstraint("document_id", "ordinal", name="uq_document_chunks_ordinal"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    document_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("assets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    embedding_norm: Mapped[float] = mapped_column(Float, nullable=False)
    provenance_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer)
    row_start: Mapped[int | None] = mapped_column(Integer)
    row_end: Mapped[int | None] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    embedding_model: Mapped[str] = mapped_column(String(255), nullable=False)
    embedding_dimensions: Mapped[int] = mapped_column(Integer, nullable=False)

    document: Mapped[Document] = relationship(back_populates="chunks", lazy="raise")
    asset: Mapped[Asset] = relationship(lazy="joined")
    citation_links: Mapped[list[MessageCitation]] = relationship(
        back_populates="chunk",
        lazy="raise",
    )


Index(
    "ix_document_chunks_owner_document_ordinal",
    DocumentChunk.owner_id,
    DocumentChunk.document_id,
    DocumentChunk.ordinal,
)
