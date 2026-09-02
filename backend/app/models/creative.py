from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


MAX_CREATIVE_EXPERIENCES_PER_OWNER = 20
MAX_CREATIVE_TURNS_PER_EXPERIENCE = 100


class CreativeExperienceMode(StrEnum):
    STORY = "story"
    GAME = "game"
    CHARACTER = "character"


class CreativeExperienceStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ARCHIVED = "archived"


def _enum(enum_type, name: str, length: int = 32):
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        create_constraint=False,
        validate_strings=True,
        values_callable=lambda value: [member.value for member in value],
        length=length,
    )


class CreativeExperience(Base):
    __tablename__ = "creative_experiences"
    __table_args__ = (
        CheckConstraint(
            "mode IN ('story', 'game', 'character')",
            name="mode_allowed",
        ),
        CheckConstraint(
            "char_length(trim(title)) BETWEEN 1 AND 160",
            name="title_bounded_nonblank",
        ),
        CheckConstraint(
            "char_length(trim(premise)) BETWEEN 1 AND 4000",
            name="premise_bounded_nonblank",
        ),
        CheckConstraint(
            "char_length(trim(genre)) BETWEEN 1 AND 80",
            name="genre_bounded_nonblank",
        ),
        CheckConstraint(
            "language ~ '^[A-Za-z][A-Za-z0-9-]{1,34}$'",
            name="language_valid",
        ),
        CheckConstraint(
            "character_name IS NULL OR char_length(trim(character_name)) BETWEEN 1 AND 120",
            name="character_name_bounded_nonblank",
        ),
        CheckConstraint(
            "(mode = 'character' AND character_name IS NOT NULL) OR mode <> 'character'",
            name="character_mode_named",
        ),
        CheckConstraint(
            "safety_tier = 'general'",
            name="safety_tier_general_only",
        ),
        CheckConstraint(
            "status IN ('active', 'completed', 'archived')",
            name="status_allowed",
        ),
        CheckConstraint(
            "turn_count BETWEEN 0 AND 100",
            name="turn_count_bounded",
        ),
        CheckConstraint(
            "(status = 'completed' AND completed_at IS NOT NULL) OR "
            "(status <> 'completed' AND completed_at IS NULL)",
            name="completion_consistent",
        ),
        UniqueConstraint("id", "owner_id", name="uq_creative_experiences_id_owner"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    mode: Mapped[CreativeExperienceMode] = mapped_column(
        _enum(CreativeExperienceMode, "creative_experience_mode"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    premise: Mapped[str] = mapped_column(String(4000), nullable=False)
    genre: Mapped[str] = mapped_column(String(80), nullable=False)
    language: Mapped[str] = mapped_column(String(35), nullable=False)
    character_name: Mapped[str | None] = mapped_column(String(120))
    safety_tier: Mapped[str] = mapped_column(String(16), nullable=False, default="general")
    status: Mapped[CreativeExperienceStatus] = mapped_column(
        _enum(CreativeExperienceStatus, "creative_experience_status"),
        nullable=False,
        default=CreativeExperienceStatus.ACTIVE,
    )
    turn_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    turns: Mapped[list[CreativeTurn]] = relationship(
        back_populates="experience",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="CreativeTurn.position",
        passive_deletes=True,
    )


class CreativeTurn(Base):
    __tablename__ = "creative_turns"
    __table_args__ = (
        CheckConstraint("position BETWEEN 1 AND 100", name="position_bounded"),
        CheckConstraint(
            "char_length(trim(owner_input)) BETWEEN 1 AND 4000",
            name="owner_input_bounded_nonblank",
        ),
        CheckConstraint(
            "char_length(trim(output)) BETWEEN 1 AND 32768",
            name="output_bounded_nonblank",
        ),
        CheckConstraint(
            "output_sha256 ~ '^[0-9a-f]{64}$'",
            name="output_sha256_valid",
        ),
        CheckConstraint(
            "char_length(model_id) BETWEEN 1 AND 96",
            name="model_id_bounded_nonblank",
        ),
        ForeignKeyConstraint(
            ("experience_id", "owner_id"),
            ("creative_experiences.id", "creative_experiences.owner_id"),
            name="fk_creative_turns_experience_owner",
            ondelete="CASCADE",
        ),
        UniqueConstraint("experience_id", "position", name="uq_creative_turns_position"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    experience_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    owner_input: Mapped[str] = mapped_column(String(4000), nullable=False)
    output: Mapped[str] = mapped_column(Text, nullable=False)
    output_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    model_id: Mapped[str] = mapped_column(String(96), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    experience: Mapped[CreativeExperience] = relationship(back_populates="turns")


Index(
    "ix_creative_experiences_owner_updated_at",
    CreativeExperience.owner_id,
    CreativeExperience.updated_at.desc(),
)
Index(
    "ix_creative_turns_experience_position",
    CreativeTurn.experience_id,
    CreativeTurn.position,
)
