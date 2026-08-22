from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.document import DOCUMENT_EMBEDDING_BYTES


MAX_MEMORY_CONTENT_CHARACTERS = 2_000


class MemoryCategory(StrEnum):
    PREFERENCE = "preference"
    FACT = "fact"
    INSTRUCTION = "instruction"
    PROJECT_CONTEXT = "project_context"


class MemorySetting(Base):
    __tablename__ = "memory_settings"

    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
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


class Memory(Base):
    __tablename__ = "memories"
    __table_args__ = (
        CheckConstraint(
            "category IN ('preference', 'fact', 'instruction', 'project_context')",
            name="category_allowed",
        ),
        CheckConstraint(
            "provenance_kind = 'explicit_user_entry'",
            name="provenance_kind_allowed",
        ),
        CheckConstraint(
            "content IS NULL OR (char_length(content) BETWEEN 1 AND 2000 "
            "AND char_length(btrim(content)) > 0)",
            name="content_bounded_non_blank",
        ),
        CheckConstraint(
            "(deleted_at IS NULL AND content IS NOT NULL "
            f"AND octet_length(embedding) = {DOCUMENT_EMBEDDING_BYTES} "
            "AND embedding_norm > 0) OR "
            "(deleted_at IS NOT NULL AND content IS NULL "
            "AND embedding IS NULL AND embedding_norm IS NULL)",
            name="active_content_embedding_consistent",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    category: Mapped[MemoryCategory] = mapped_column(
        Enum(
            MemoryCategory,
            name="memory_category",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=lambda enum_type: [member.value for member in enum_type],
            length=32,
        ),
        nullable=False,
    )
    content: Mapped[str | None] = mapped_column(Text)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary)
    embedding_norm: Mapped[float | None] = mapped_column(Float)
    provenance_kind: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="explicit_user_entry",
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
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index("ix_memories_owner_updated_at", Memory.owner_id, Memory.updated_at.desc())
Index("ix_memories_owner_category", Memory.owner_id, Memory.category)
