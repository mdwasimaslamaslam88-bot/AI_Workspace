from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


MAX_LEARNING_PROGRAMS_PER_OWNER = 20
MAX_LESSONS_PER_PROGRAM = 50
MAX_ACTIVITIES_PER_LESSON = 30
MAX_REVIEW_ITEMS_PER_PROGRAM = 500


class LearningProgramStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class LearningLessonStatus(StrEnum):
    PLANNED = "planned"
    READY = "ready"
    COMPLETED = "completed"


class LearningActivityKind(StrEnum):
    EXERCISE = "exercise"
    QUIZ = "quiz"
    CONVERSATION = "conversation"
    REVISION = "revision"


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


class LearningProgram(Base):
    __tablename__ = "learning_programs"
    __table_args__ = (
        CheckConstraint(
            "char_length(trim(subject)) BETWEEN 1 AND 160",
            name="subject_bounded_nonblank",
        ),
        CheckConstraint(
            "char_length(trim(goal)) BETWEEN 1 AND 2000",
            name="goal_bounded_nonblank",
        ),
        CheckConstraint(
            "target_language ~ '^[A-Za-z][A-Za-z0-9-]{1,34}$'",
            name="target_language_valid",
        ),
        CheckConstraint(
            "instruction_language ~ '^[A-Za-z][A-Za-z0-9-]{1,34}$'",
            name="instruction_language_valid",
        ),
        CheckConstraint(
            "start_difficulty BETWEEN 1 AND 5 AND current_difficulty BETWEEN 1 AND 5 "
            "AND target_difficulty BETWEEN 1 AND 5",
            name="difficulty_bounded",
        ),
        CheckConstraint(
            "start_difficulty <= target_difficulty",
            name="difficulty_progression_valid",
        ),
        CheckConstraint(
            "weekly_minutes BETWEEN 15 AND 10080",
            name="weekly_minutes_bounded",
        ),
        CheckConstraint(
            "status IN ('active', 'completed', 'archived')",
            name="status_allowed",
        ),
        CheckConstraint(
            "completed_lessons BETWEEN 0 AND total_lessons AND total_lessons BETWEEN 1 AND 50",
            name="lesson_progress_valid",
        ),
        CheckConstraint(
            "total_attempts BETWEEN 0 AND 1000000 AND correct_attempts BETWEEN 0 AND total_attempts",
            name="attempt_progress_valid",
        ),
        UniqueConstraint("id", "owner_id", name="uq_learning_programs_id_owner"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    subject: Mapped[str] = mapped_column(String(160), nullable=False)
    goal: Mapped[str] = mapped_column(String(2000), nullable=False)
    target_language: Mapped[str] = mapped_column(String(35), nullable=False)
    instruction_language: Mapped[str] = mapped_column(String(35), nullable=False)
    start_difficulty: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    current_difficulty: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    target_difficulty: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    weekly_minutes: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    adaptive_difficulty: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    status: Mapped[LearningProgramStatus] = mapped_column(
        _enum(LearningProgramStatus, "learning_program_status"),
        nullable=False,
        default=LearningProgramStatus.ACTIVE,
    )
    total_lessons: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=5)
    completed_lessons: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    total_attempts: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    correct_attempts: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    lessons: Mapped[list[LearningLesson]] = relationship(
        back_populates="program",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="LearningLesson.position",
        passive_deletes=True,
    )
    review_items: Mapped[list[LearningReviewItem]] = relationship(
        back_populates="program",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="LearningReviewItem.created_at",
        passive_deletes=True,
    )


class LearningLesson(Base):
    __tablename__ = "learning_lessons"
    __table_args__ = (
        CheckConstraint("position BETWEEN 1 AND 50", name="position_bounded"),
        CheckConstraint(
            "char_length(trim(title)) BETWEEN 1 AND 160",
            name="title_bounded_nonblank",
        ),
        CheckConstraint(
            "char_length(objectives_json) BETWEEN 2 AND 4096",
            name="objectives_json_bounded",
        ),
        CheckConstraint("difficulty BETWEEN 1 AND 5", name="difficulty_bounded"),
        CheckConstraint(
            "status IN ('planned', 'ready', 'completed')", name="status_allowed"
        ),
        CheckConstraint(
            "(status = 'planned' AND content IS NULL AND output_sha256 IS NULL AND model_id IS NULL "
            "AND generated_at IS NULL AND completed_at IS NULL) OR "
            "(status = 'ready' AND char_length(content) BETWEEN 1 AND 65536 "
            "AND output_sha256 ~ '^[0-9a-f]{64}$' AND char_length(model_id) BETWEEN 1 AND 96 "
            "AND generated_at IS NOT NULL AND completed_at IS NULL) OR "
            "(status = 'completed' AND char_length(content) BETWEEN 1 AND 65536 "
            "AND output_sha256 ~ '^[0-9a-f]{64}$' AND char_length(model_id) BETWEEN 1 AND 96 "
            "AND generated_at IS NOT NULL AND completed_at IS NOT NULL)",
            name="content_lifecycle_consistent",
        ),
        CheckConstraint(
            "char_length(memory_ids_json) BETWEEN 2 AND 2048",
            name="memory_ids_json_bounded",
        ),
        CheckConstraint(
            "score_bps IS NULL OR score_bps BETWEEN 0 AND 10000",
            name="score_bounded",
        ),
        ForeignKeyConstraint(
            ("program_id", "owner_id"),
            ("learning_programs.id", "learning_programs.owner_id"),
            name="fk_learning_lessons_program_owner",
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "program_id", "owner_id", name="uq_learning_lessons_identity"),
        UniqueConstraint("program_id", "position", name="uq_learning_lessons_position"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    program_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    objectives_json: Mapped[str] = mapped_column(String(4096), nullable=False)
    difficulty: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    status: Mapped[LearningLessonStatus] = mapped_column(
        _enum(LearningLessonStatus, "learning_lesson_status"),
        nullable=False,
        default=LearningLessonStatus.PLANNED,
    )
    content: Mapped[str | None] = mapped_column(Text)
    output_sha256: Mapped[str | None] = mapped_column(String(64))
    model_id: Mapped[str | None] = mapped_column(String(96))
    memory_ids_json: Mapped[str] = mapped_column(String(2048), nullable=False, default="[]")
    score_bps: Mapped[int | None] = mapped_column(SmallInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    program: Mapped[LearningProgram] = relationship(back_populates="lessons")
    activities: Mapped[list[LearningActivity]] = relationship(
        back_populates="lesson",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="LearningActivity.created_at",
        passive_deletes=True,
    )


class LearningActivity(Base):
    __tablename__ = "learning_activities"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('exercise', 'quiz', 'conversation', 'revision')",
            name="kind_allowed",
        ),
        CheckConstraint(
            "char_length(trim(prompt)) BETWEEN 1 AND 4000",
            name="prompt_bounded_nonblank",
        ),
        CheckConstraint(
            "expected_answer_sha256 ~ '^[0-9a-f]{64}$'",
            name="expected_answer_sha256_valid",
        ),
        CheckConstraint(
            "char_length(trim(explanation)) BETWEEN 1 AND 4000",
            name="explanation_bounded_nonblank",
        ),
        CheckConstraint("difficulty BETWEEN 1 AND 5", name="difficulty_bounded"),
        CheckConstraint("max_attempts BETWEEN 1 AND 10", name="max_attempts_bounded"),
        ForeignKeyConstraint(
            ("lesson_id", "program_id", "owner_id"),
            ("learning_lessons.id", "learning_lessons.program_id", "learning_lessons.owner_id"),
            name="fk_learning_activities_lesson_program_owner",
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "program_id", "owner_id", name="uq_learning_activities_identity"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    lesson_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    program_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    kind: Mapped[LearningActivityKind] = mapped_column(
        _enum(LearningActivityKind, "learning_activity_kind"), nullable=False
    )
    prompt: Mapped[str] = mapped_column(String(4000), nullable=False)
    expected_answer_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    explanation: Mapped[str] = mapped_column(String(4000), nullable=False)
    difficulty: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    max_attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=3)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    lesson: Mapped[LearningLesson] = relationship(back_populates="activities")
    attempts: Mapped[list[LearningAttempt]] = relationship(
        back_populates="activity",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="LearningAttempt.created_at",
        passive_deletes=True,
    )


class LearningAttempt(Base):
    __tablename__ = "learning_attempts"
    __table_args__ = (
        CheckConstraint("answer_sha256 ~ '^[0-9a-f]{64}$'", name="answer_sha256_valid"),
        CheckConstraint("score_bps IN (0, 10000)", name="score_allowed"),
        CheckConstraint(
            "(is_correct AND score_bps = 10000) OR (NOT is_correct AND score_bps = 0)",
            name="result_consistent",
        ),
        CheckConstraint(
            "char_length(trim(feedback)) BETWEEN 1 AND 4000",
            name="feedback_bounded_nonblank",
        ),
        ForeignKeyConstraint(
            ("activity_id", "program_id", "owner_id"),
            ("learning_activities.id", "learning_activities.program_id", "learning_activities.owner_id"),
            name="fk_learning_attempts_activity_program_owner",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    activity_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    program_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    answer_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    score_bps: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    feedback: Mapped[str] = mapped_column(String(4000), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    activity: Mapped[LearningActivity] = relationship(back_populates="attempts")


class LearningReviewItem(Base):
    __tablename__ = "learning_review_items"
    __table_args__ = (
        CheckConstraint(
            "char_length(trim(front)) BETWEEN 1 AND 1000",
            name="front_bounded_nonblank",
        ),
        CheckConstraint(
            "char_length(trim(back)) BETWEEN 1 AND 2000",
            name="back_bounded_nonblank",
        ),
        CheckConstraint("interval_days BETWEEN 0 AND 36500", name="interval_bounded"),
        CheckConstraint("ease_milli BETWEEN 1300 AND 3000", name="ease_bounded"),
        CheckConstraint("repetitions BETWEEN 0 AND 10000", name="repetitions_bounded"),
        CheckConstraint(
            "last_quality IS NULL OR last_quality BETWEEN 0 AND 5",
            name="last_quality_bounded",
        ),
        ForeignKeyConstraint(
            ("program_id", "owner_id"),
            ("learning_programs.id", "learning_programs.owner_id"),
            name="fk_learning_review_items_program_owner",
            ondelete="CASCADE",
        ),
        UniqueConstraint("program_id", "front", name="uq_learning_review_items_front"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    program_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    front: Mapped[str] = mapped_column(String(1000), nullable=False)
    back: Mapped[str] = mapped_column(String(2000), nullable=False)
    interval_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ease_milli: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=2500)
    repetitions: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    due_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_quality: Mapped[int | None] = mapped_column(SmallInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    program: Mapped[LearningProgram] = relationship(back_populates="review_items")


Index("ix_learning_programs_owner_updated_at", LearningProgram.owner_id, LearningProgram.updated_at.desc())
Index("ix_learning_lessons_program_position", LearningLesson.program_id, LearningLesson.position)
Index("ix_learning_attempts_program_created_at", LearningAttempt.program_id, LearningAttempt.created_at.desc())
Index("ix_learning_review_items_program_due_at", LearningReviewItem.program_id, LearningReviewItem.due_at)
