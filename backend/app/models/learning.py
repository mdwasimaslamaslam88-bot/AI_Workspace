from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
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
MAX_SKILLS_PER_PROGRAM = 100
MAX_SOURCES_PER_PROGRAM = 8
MAX_SESSIONS_PER_PROGRAM = 500


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
    MCQ = "mcq"
    SHORT_ANSWER = "short_answer"
    LONG_ANSWER = "long_answer"
    CODING = "coding"
    ASSIGNMENT = "assignment"


class LearningGradingMode(StrEnum):
    EXACT = "exact"
    RUBRIC = "rubric"


class LearningTeachingMode(StrEnum):
    TEACHER = "teacher"
    SOCRATIC = "socratic"
    COACH = "coach"
    MENTOR = "mentor"
    INTERVIEWER = "interviewer"
    PAIR_PROGRAMMING = "pair_programming"
    STUDY = "study"
    FOCUS = "focus"
    EXAM = "exam"
    REVISION = "revision"


class LearningSessionStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"


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
        CheckConstraint(
            "teaching_mode IN ('teacher', 'socratic', 'coach', 'mentor', 'interviewer', "
            "'pair_programming', 'study', 'focus', 'exam', 'revision')",
            name="teaching_mode_allowed",
        ),
        CheckConstraint(
            "char_length(preferences_json) BETWEEN 2 AND 2048",
            name="preferences_json_bounded",
        ),
        CheckConstraint(
            "current_streak_days BETWEEN 0 AND 36500 AND best_streak_days BETWEEN current_streak_days AND 36500",
            name="streak_bounded",
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
    teaching_mode: Mapped[LearningTeachingMode] = mapped_column(
        _enum(LearningTeachingMode, "learning_teaching_mode"),
        nullable=False,
        default=LearningTeachingMode.TEACHER,
        server_default=text("'teacher'"),
    )
    preferences_json: Mapped[str] = mapped_column(
        String(2048), nullable=False, default="{}", server_default=text("'{}'")
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
    current_streak_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    best_streak_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    last_study_date: Mapped[date | None] = mapped_column(Date)
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
    skills: Mapped[list[LearningSkill]] = relationship(
        back_populates="program",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="LearningSkill.name",
        passive_deletes=True,
    )
    sources: Mapped[list[LearningSource]] = relationship(
        back_populates="program",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="LearningSource.created_at",
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
            "char_length(source_ids_json) BETWEEN 2 AND 2048",
            name="source_ids_json_bounded",
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
    source_ids_json: Mapped[str] = mapped_column(
        String(2048), nullable=False, default="[]", server_default=text("'[]'")
    )
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
            "kind IN ('exercise', 'quiz', 'conversation', 'revision', 'mcq', "
            "'short_answer', 'long_answer', 'coding', 'assignment')",
            name="kind_allowed",
        ),
        CheckConstraint("grading_mode IN ('exact', 'rubric')", name="grading_mode_allowed"),
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
        CheckConstraint(
            "char_length(trim(skill_name)) BETWEEN 1 AND 160",
            name="skill_name_bounded_nonblank",
        ),
        CheckConstraint("char_length(hints_json) BETWEEN 2 AND 4096", name="hints_json_bounded"),
        CheckConstraint("char_length(rubric_json) BETWEEN 2 AND 4096", name="rubric_json_bounded"),
        CheckConstraint("char_length(source_ids_json) BETWEEN 2 AND 2048", name="source_ids_json_bounded"),
        CheckConstraint("hints_requested BETWEEN 0 AND 10", name="hints_requested_bounded"),
        CheckConstraint(
            "generation_sha256 IS NULL OR generation_sha256 ~ '^[0-9a-f]{64}$'",
            name="generation_sha256_valid",
        ),
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
    grading_mode: Mapped[LearningGradingMode] = mapped_column(
        _enum(LearningGradingMode, "learning_grading_mode"),
        nullable=False,
        default=LearningGradingMode.EXACT,
        server_default=text("'exact'"),
    )
    prompt: Mapped[str] = mapped_column(String(4000), nullable=False)
    expected_answer_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    explanation: Mapped[str] = mapped_column(String(4000), nullable=False)
    difficulty: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    max_attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=3)
    skill_name: Mapped[str] = mapped_column(String(160), nullable=False, default="General")
    hints_json: Mapped[str] = mapped_column(String(4096), nullable=False, default="[]")
    rubric_json: Mapped[str] = mapped_column(String(4096), nullable=False, default="[]")
    source_ids_json: Mapped[str] = mapped_column(String(2048), nullable=False, default="[]")
    hints_requested: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    generation_sha256: Mapped[str | None] = mapped_column(String(64))
    model_id: Mapped[str | None] = mapped_column(String(96))
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
        CheckConstraint("score_bps BETWEEN 0 AND 10000", name="score_allowed"),
        CheckConstraint(
            "(is_correct AND score_bps >= 7000) OR (NOT is_correct AND score_bps < 7000)",
            name="result_consistent",
        ),
        CheckConstraint(
            "mistake_code IS NULL OR mistake_code ~ '^[a-z0-9_]{1,64}$'",
            name="mistake_code_safe",
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
    mistake_code: Mapped[str | None] = mapped_column(String(64))
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


class LearningSkill(Base):
    __tablename__ = "learning_skills"
    __table_args__ = (
        CheckConstraint("char_length(trim(name)) BETWEEN 1 AND 160", name="name_bounded_nonblank"),
        CheckConstraint("mastery_bps BETWEEN 0 AND 10000", name="mastery_bounded"),
        CheckConstraint("confidence_bps BETWEEN 0 AND 10000", name="confidence_bounded"),
        CheckConstraint("attempts BETWEEN 0 AND 1000000", name="attempts_bounded"),
        CheckConstraint("last_score_bps BETWEEN 0 AND 10000", name="last_score_bounded"),
        CheckConstraint("mistake_count BETWEEN 0 AND attempts", name="mistakes_bounded"),
        ForeignKeyConstraint(
            ("program_id", "owner_id"),
            ("learning_programs.id", "learning_programs.owner_id"),
            name="fk_learning_skills_program_owner",
            ondelete="CASCADE",
        ),
        UniqueConstraint("program_id", "name", name="uq_learning_skills_program_name"),
        UniqueConstraint("id", "program_id", "owner_id", name="uq_learning_skills_identity"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    program_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    mastery_bps: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    confidence_bps: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mistake_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_score_bps: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    last_practiced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    program: Mapped[LearningProgram] = relationship(back_populates="skills")


class LearningSource(Base):
    __tablename__ = "learning_sources"
    __table_args__ = (
        CheckConstraint("char_length(trim(label)) BETWEEN 1 AND 255", name="label_bounded_nonblank"),
        CheckConstraint("source_sha256 ~ '^[0-9a-f]{64}$'", name="source_sha256_valid"),
        ForeignKeyConstraint(
            ("program_id", "owner_id"),
            ("learning_programs.id", "learning_programs.owner_id"),
            name="fk_learning_sources_program_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ("document_id", "owner_id"),
            ("documents.id", "documents.owner_id"),
            name="fk_learning_sources_document_owner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("asset_id", "owner_id"),
            ("assets.id", "assets.owner_id"),
            name="fk_learning_sources_asset_owner",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("program_id", "document_id", name="uq_learning_sources_program_document"),
        UniqueConstraint("id", "program_id", "owner_id", name="uq_learning_sources_identity"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    program_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    document_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    asset_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    program: Mapped[LearningProgram] = relationship(back_populates="sources")


class LearningSession(Base):
    __tablename__ = "learning_sessions"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'paused', 'completed')", name="status_allowed"),
        CheckConstraint(
            "mode IN ('teacher', 'socratic', 'coach', 'mentor', 'interviewer', "
            "'pair_programming', 'study', 'focus', 'exam', 'revision')",
            name="mode_allowed",
        ),
        CheckConstraint("char_length(trim(focus)) BETWEEN 1 AND 500", name="focus_bounded_nonblank"),
        CheckConstraint("planned_minutes BETWEEN 5 AND 480", name="planned_minutes_bounded"),
        CheckConstraint("interruption_count BETWEEN 0 AND 10000", name="interruptions_bounded"),
        CheckConstraint(
            "(status = 'active' AND paused_at IS NULL AND completed_at IS NULL) OR "
            "(status = 'paused' AND paused_at IS NOT NULL AND completed_at IS NULL) OR "
            "(status = 'completed' AND completed_at IS NOT NULL)",
            name="lifecycle_consistent",
        ),
        ForeignKeyConstraint(
            ("program_id", "owner_id"),
            ("learning_programs.id", "learning_programs.owner_id"),
            name="fk_learning_sessions_program_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ("current_lesson_id", "program_id", "owner_id"),
            ("learning_lessons.id", "learning_lessons.program_id", "learning_lessons.owner_id"),
            name="fk_learning_sessions_lesson_program_owner",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "program_id", "owner_id", name="uq_learning_sessions_identity"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    program_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    current_lesson_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    mode: Mapped[LearningTeachingMode] = mapped_column(_enum(LearningTeachingMode, "learning_session_mode"), nullable=False)
    status: Mapped[LearningSessionStatus] = mapped_column(_enum(LearningSessionStatus, "learning_session_status"), nullable=False, default=LearningSessionStatus.ACTIVE)
    focus: Mapped[str] = mapped_column(String(500), nullable=False)
    planned_minutes: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    interruption_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LearningEvent(Base):
    __tablename__ = "learning_events"
    __table_args__ = (
        CheckConstraint("action ~ '^[a-z][a-z0-9_]{1,63}$'", name="action_safe"),
        CheckConstraint("entity_kind ~ '^[a-z][a-z0-9_]{1,31}$'", name="entity_kind_safe"),
        CheckConstraint("metadata_sha256 ~ '^[0-9a-f]{64}$'", name="metadata_sha256_valid"),
        ForeignKeyConstraint(
            ("program_id", "owner_id"),
            ("learning_programs.id", "learning_programs.owner_id"),
            name="fk_learning_events_program_owner",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    program_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    metadata_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


Index("ix_learning_programs_owner_updated_at", LearningProgram.owner_id, LearningProgram.updated_at.desc())
Index("ix_learning_lessons_program_position", LearningLesson.program_id, LearningLesson.position)
Index("ix_learning_attempts_program_created_at", LearningAttempt.program_id, LearningAttempt.created_at.desc())
Index("ix_learning_review_items_program_due_at", LearningReviewItem.program_id, LearningReviewItem.due_at)
Index("ix_learning_skills_program_mastery", LearningSkill.program_id, LearningSkill.mastery_bps)
Index("ix_learning_sessions_program_started_at", LearningSession.program_id, LearningSession.started_at.desc())
Index(
    "uq_learning_sessions_open_program",
    LearningSession.program_id,
    unique=True,
    postgresql_where=text("status IN ('active', 'paused')"),
)
Index("ix_learning_events_program_created_at", LearningEvent.program_id, LearningEvent.created_at.desc())
