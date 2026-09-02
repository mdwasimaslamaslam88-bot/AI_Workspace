"""Add persistent adaptive learning programs and spaced repetition.

Revision ID: 0016_learning_system
Revises: 0015_finance_intelligence
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0016_learning_system"
down_revision: Union[str, None] = "0015_finance_intelligence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _enum(*values: str, name: str, length: int = 32) -> sa.Enum:
    return sa.Enum(
        *values,
        name=name,
        native_enum=False,
        create_constraint=False,
        length=length,
    )


def upgrade() -> None:
    op.create_table(
        "learning_programs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("subject", sa.String(length=160), nullable=False),
        sa.Column("goal", sa.String(length=2000), nullable=False),
        sa.Column("target_language", sa.String(length=35), nullable=False),
        sa.Column("instruction_language", sa.String(length=35), nullable=False),
        sa.Column("start_difficulty", sa.SmallInteger(), nullable=False),
        sa.Column("current_difficulty", sa.SmallInteger(), nullable=False),
        sa.Column("target_difficulty", sa.SmallInteger(), nullable=False),
        sa.Column("weekly_minutes", sa.SmallInteger(), nullable=False),
        sa.Column("adaptive_difficulty", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "status",
            _enum("active", "completed", "archived", name="learning_program_status"),
            nullable=False,
        ),
        sa.Column("total_lessons", sa.SmallInteger(), nullable=False),
        sa.Column("completed_lessons", sa.SmallInteger(), nullable=False),
        sa.Column("total_attempts", sa.BigInteger(), nullable=False),
        sa.Column("correct_attempts", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("char_length(trim(subject)) BETWEEN 1 AND 160", name=op.f("ck_learning_programs_subject_bounded_nonblank")),
        sa.CheckConstraint("char_length(trim(goal)) BETWEEN 1 AND 2000", name=op.f("ck_learning_programs_goal_bounded_nonblank")),
        sa.CheckConstraint("target_language ~ '^[A-Za-z][A-Za-z0-9-]{1,34}$'", name=op.f("ck_learning_programs_target_language_valid")),
        sa.CheckConstraint("instruction_language ~ '^[A-Za-z][A-Za-z0-9-]{1,34}$'", name=op.f("ck_learning_programs_instruction_language_valid")),
        sa.CheckConstraint("start_difficulty BETWEEN 1 AND 5 AND current_difficulty BETWEEN 1 AND 5 AND target_difficulty BETWEEN 1 AND 5", name=op.f("ck_learning_programs_difficulty_bounded")),
        sa.CheckConstraint("start_difficulty <= target_difficulty", name=op.f("ck_learning_programs_difficulty_progression_valid")),
        sa.CheckConstraint("weekly_minutes BETWEEN 15 AND 10080", name=op.f("ck_learning_programs_weekly_minutes_bounded")),
        sa.CheckConstraint("status IN ('active', 'completed', 'archived')", name=op.f("ck_learning_programs_status_allowed")),
        sa.CheckConstraint("completed_lessons BETWEEN 0 AND total_lessons AND total_lessons BETWEEN 1 AND 50", name=op.f("ck_learning_programs_lesson_progress_valid")),
        sa.CheckConstraint("total_attempts BETWEEN 0 AND 1000000 AND correct_attempts BETWEEN 0 AND total_attempts", name=op.f("ck_learning_programs_attempt_progress_valid")),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name=op.f("fk_learning_programs_owner_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_learning_programs")),
        sa.UniqueConstraint("id", "owner_id", name="uq_learning_programs_id_owner"),
    )
    op.create_table(
        "learning_lessons",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("program_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("objectives_json", sa.String(length=4096), nullable=False),
        sa.Column("difficulty", sa.SmallInteger(), nullable=False),
        sa.Column("status", _enum("planned", "ready", "completed", name="learning_lesson_status"), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("output_sha256", sa.String(length=64), nullable=True),
        sa.Column("model_id", sa.String(length=96), nullable=True),
        sa.Column("memory_ids_json", sa.String(length=2048), nullable=False),
        sa.Column("score_bps", sa.SmallInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("position BETWEEN 1 AND 50", name=op.f("ck_learning_lessons_position_bounded")),
        sa.CheckConstraint("char_length(trim(title)) BETWEEN 1 AND 160", name=op.f("ck_learning_lessons_title_bounded_nonblank")),
        sa.CheckConstraint("char_length(objectives_json) BETWEEN 2 AND 4096", name=op.f("ck_learning_lessons_objectives_json_bounded")),
        sa.CheckConstraint("difficulty BETWEEN 1 AND 5", name=op.f("ck_learning_lessons_difficulty_bounded")),
        sa.CheckConstraint("status IN ('planned', 'ready', 'completed')", name=op.f("ck_learning_lessons_status_allowed")),
        sa.CheckConstraint("(status = 'planned' AND content IS NULL AND output_sha256 IS NULL AND model_id IS NULL AND generated_at IS NULL AND completed_at IS NULL) OR (status = 'ready' AND char_length(content) BETWEEN 1 AND 65536 AND output_sha256 ~ '^[0-9a-f]{64}$' AND char_length(model_id) BETWEEN 1 AND 96 AND generated_at IS NOT NULL AND completed_at IS NULL) OR (status = 'completed' AND char_length(content) BETWEEN 1 AND 65536 AND output_sha256 ~ '^[0-9a-f]{64}$' AND char_length(model_id) BETWEEN 1 AND 96 AND generated_at IS NOT NULL AND completed_at IS NOT NULL)", name=op.f("ck_learning_lessons_content_lifecycle_consistent")),
        sa.CheckConstraint("char_length(memory_ids_json) BETWEEN 2 AND 2048", name=op.f("ck_learning_lessons_memory_ids_json_bounded")),
        sa.CheckConstraint("score_bps IS NULL OR score_bps BETWEEN 0 AND 10000", name=op.f("ck_learning_lessons_score_bounded")),
        sa.ForeignKeyConstraint(["program_id", "owner_id"], ["learning_programs.id", "learning_programs.owner_id"], name="fk_learning_lessons_program_owner", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_learning_lessons")),
        sa.UniqueConstraint("id", "program_id", "owner_id", name="uq_learning_lessons_identity"),
        sa.UniqueConstraint("program_id", "position", name="uq_learning_lessons_position"),
    )
    op.create_table(
        "learning_activities",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("lesson_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("program_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("kind", _enum("exercise", "quiz", "conversation", "revision", name="learning_activity_kind"), nullable=False),
        sa.Column("prompt", sa.String(length=4000), nullable=False),
        sa.Column("expected_answer_sha256", sa.String(length=64), nullable=False),
        sa.Column("explanation", sa.String(length=4000), nullable=False),
        sa.Column("difficulty", sa.SmallInteger(), nullable=False),
        sa.Column("max_attempts", sa.SmallInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("kind IN ('exercise', 'quiz', 'conversation', 'revision')", name=op.f("ck_learning_activities_kind_allowed")),
        sa.CheckConstraint("char_length(trim(prompt)) BETWEEN 1 AND 4000", name=op.f("ck_learning_activities_prompt_bounded_nonblank")),
        sa.CheckConstraint("expected_answer_sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_learning_activities_expected_answer_sha256_valid")),
        sa.CheckConstraint("char_length(trim(explanation)) BETWEEN 1 AND 4000", name=op.f("ck_learning_activities_explanation_bounded_nonblank")),
        sa.CheckConstraint("difficulty BETWEEN 1 AND 5", name=op.f("ck_learning_activities_difficulty_bounded")),
        sa.CheckConstraint("max_attempts BETWEEN 1 AND 10", name=op.f("ck_learning_activities_max_attempts_bounded")),
        sa.ForeignKeyConstraint(["lesson_id", "program_id", "owner_id"], ["learning_lessons.id", "learning_lessons.program_id", "learning_lessons.owner_id"], name="fk_learning_activities_lesson_program_owner", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_learning_activities")),
        sa.UniqueConstraint("id", "program_id", "owner_id", name="uq_learning_activities_identity"),
    )
    op.create_table(
        "learning_attempts",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("activity_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("program_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("answer_sha256", sa.String(length=64), nullable=False),
        sa.Column("is_correct", sa.Boolean(), nullable=False),
        sa.Column("score_bps", sa.SmallInteger(), nullable=False),
        sa.Column("feedback", sa.String(length=4000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("answer_sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_learning_attempts_answer_sha256_valid")),
        sa.CheckConstraint("score_bps IN (0, 10000)", name=op.f("ck_learning_attempts_score_allowed")),
        sa.CheckConstraint("(is_correct AND score_bps = 10000) OR (NOT is_correct AND score_bps = 0)", name=op.f("ck_learning_attempts_result_consistent")),
        sa.CheckConstraint("char_length(trim(feedback)) BETWEEN 1 AND 4000", name=op.f("ck_learning_attempts_feedback_bounded_nonblank")),
        sa.ForeignKeyConstraint(["activity_id", "program_id", "owner_id"], ["learning_activities.id", "learning_activities.program_id", "learning_activities.owner_id"], name="fk_learning_attempts_activity_program_owner", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_learning_attempts")),
    )
    op.create_table(
        "learning_review_items",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("program_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("front", sa.String(length=1000), nullable=False),
        sa.Column("back", sa.String(length=2000), nullable=False),
        sa.Column("interval_days", sa.Integer(), nullable=False),
        sa.Column("ease_milli", sa.SmallInteger(), nullable=False),
        sa.Column("repetitions", sa.SmallInteger(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_quality", sa.SmallInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("char_length(trim(front)) BETWEEN 1 AND 1000", name=op.f("ck_learning_review_items_front_bounded_nonblank")),
        sa.CheckConstraint("char_length(trim(back)) BETWEEN 1 AND 2000", name=op.f("ck_learning_review_items_back_bounded_nonblank")),
        sa.CheckConstraint("interval_days BETWEEN 0 AND 36500", name=op.f("ck_learning_review_items_interval_bounded")),
        sa.CheckConstraint("ease_milli BETWEEN 1300 AND 3000", name=op.f("ck_learning_review_items_ease_bounded")),
        sa.CheckConstraint("repetitions BETWEEN 0 AND 10000", name=op.f("ck_learning_review_items_repetitions_bounded")),
        sa.CheckConstraint("last_quality IS NULL OR last_quality BETWEEN 0 AND 5", name=op.f("ck_learning_review_items_last_quality_bounded")),
        sa.ForeignKeyConstraint(["program_id", "owner_id"], ["learning_programs.id", "learning_programs.owner_id"], name="fk_learning_review_items_program_owner", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_learning_review_items")),
        sa.UniqueConstraint("program_id", "front", name="uq_learning_review_items_front"),
    )
    op.create_index("ix_learning_programs_owner_updated_at", "learning_programs", ["owner_id", sa.text("updated_at DESC")], unique=False)
    op.create_index("ix_learning_lessons_program_position", "learning_lessons", ["program_id", "position"], unique=False)
    op.create_index("ix_learning_attempts_program_created_at", "learning_attempts", ["program_id", sa.text("created_at DESC")], unique=False)
    op.create_index("ix_learning_review_items_program_due_at", "learning_review_items", ["program_id", "due_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_learning_review_items_program_due_at", table_name="learning_review_items")
    op.drop_index("ix_learning_attempts_program_created_at", table_name="learning_attempts")
    op.drop_index("ix_learning_lessons_program_position", table_name="learning_lessons")
    op.drop_index("ix_learning_programs_owner_updated_at", table_name="learning_programs")
    op.drop_table("learning_review_items")
    op.drop_table("learning_attempts")
    op.drop_table("learning_activities")
    op.drop_table("learning_lessons")
    op.drop_table("learning_programs")
