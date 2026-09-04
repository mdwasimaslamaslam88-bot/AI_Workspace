"""Add resumable, grounded and auditable learning state.

Revision ID: 0021_learning_knowledge_os
Revises: 0020_trading_safety
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0021_learning_knowledge_os"
down_revision: Union[str, None] = "0020_trading_safety"
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


TEACHING_MODES = (
    "teacher",
    "socratic",
    "coach",
    "mentor",
    "interviewer",
    "pair_programming",
    "study",
    "focus",
    "exam",
    "revision",
)


def upgrade() -> None:
    op.create_unique_constraint(op.f("uq_documents_id_owner"), "documents", ["id", "owner_id"])

    op.add_column(
        "learning_programs",
        sa.Column(
            "teaching_mode",
            _enum(*TEACHING_MODES, name="learning_teaching_mode"),
            server_default=sa.text("'teacher'"),
            nullable=False,
        ),
    )
    op.add_column(
        "learning_programs",
        sa.Column("preferences_json", sa.String(length=2048), server_default=sa.text("'{}'"), nullable=False),
    )
    op.add_column(
        "learning_programs",
        sa.Column("current_streak_days", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "learning_programs",
        sa.Column("best_streak_days", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("learning_programs", sa.Column("last_study_date", sa.Date(), nullable=True))
    op.create_check_constraint(
        op.f("ck_learning_programs_teaching_mode_allowed"),
        "learning_programs",
        "teaching_mode IN ('teacher', 'socratic', 'coach', 'mentor', 'interviewer', "
        "'pair_programming', 'study', 'focus', 'exam', 'revision')",
    )
    op.create_check_constraint(
        op.f("ck_learning_programs_preferences_json_bounded"),
        "learning_programs",
        "char_length(preferences_json) BETWEEN 2 AND 2048",
    )
    op.create_check_constraint(
        op.f("ck_learning_programs_streak_bounded"),
        "learning_programs",
        "current_streak_days BETWEEN 0 AND 36500 AND "
        "best_streak_days BETWEEN current_streak_days AND 36500",
    )

    op.add_column(
        "learning_lessons",
        sa.Column("source_ids_json", sa.String(length=2048), server_default=sa.text("'[]'"), nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_learning_lessons_source_ids_json_bounded"),
        "learning_lessons",
        "char_length(source_ids_json) BETWEEN 2 AND 2048",
    )

    op.drop_constraint(op.f("ck_learning_activities_kind_allowed"), "learning_activities", type_="check")
    op.create_check_constraint(
        op.f("ck_learning_activities_kind_allowed"),
        "learning_activities",
        "kind IN ('exercise', 'quiz', 'conversation', 'revision', 'mcq', "
        "'short_answer', 'long_answer', 'coding', 'assignment')",
    )
    op.add_column(
        "learning_activities",
        sa.Column(
            "grading_mode",
            _enum("exact", "rubric", name="learning_grading_mode"),
            server_default=sa.text("'exact'"),
            nullable=False,
        ),
    )
    op.add_column(
        "learning_activities",
        sa.Column("skill_name", sa.String(length=160), server_default="General", nullable=False),
    )
    op.add_column(
        "learning_activities",
        sa.Column("hints_json", sa.String(length=4096), server_default="[]", nullable=False),
    )
    op.add_column(
        "learning_activities",
        sa.Column("rubric_json", sa.String(length=4096), server_default="[]", nullable=False),
    )
    op.add_column(
        "learning_activities",
        sa.Column("source_ids_json", sa.String(length=2048), server_default="[]", nullable=False),
    )
    op.add_column(
        "learning_activities",
        sa.Column("hints_requested", sa.SmallInteger(), server_default="0", nullable=False),
    )
    op.add_column(
        "learning_activities",
        sa.Column("required", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.add_column("learning_activities", sa.Column("generation_sha256", sa.String(length=64), nullable=True))
    op.add_column("learning_activities", sa.Column("model_id", sa.String(length=96), nullable=True))
    op.create_check_constraint(
        op.f("ck_learning_activities_grading_mode_allowed"),
        "learning_activities",
        "grading_mode IN ('exact', 'rubric')",
    )
    op.create_check_constraint(
        op.f("ck_learning_activities_skill_name_bounded_nonblank"),
        "learning_activities",
        "char_length(trim(skill_name)) BETWEEN 1 AND 160",
    )
    op.create_check_constraint(op.f("ck_learning_activities_hints_json_bounded"), "learning_activities", "char_length(hints_json) BETWEEN 2 AND 4096")
    op.create_check_constraint(op.f("ck_learning_activities_rubric_json_bounded"), "learning_activities", "char_length(rubric_json) BETWEEN 2 AND 4096")
    op.create_check_constraint(op.f("ck_learning_activities_source_ids_json_bounded"), "learning_activities", "char_length(source_ids_json) BETWEEN 2 AND 2048")
    op.create_check_constraint(op.f("ck_learning_activities_hints_requested_bounded"), "learning_activities", "hints_requested BETWEEN 0 AND 10")
    op.create_check_constraint(
        op.f("ck_learning_activities_generation_sha256_valid"),
        "learning_activities",
        "generation_sha256 IS NULL OR generation_sha256 ~ '^[0-9a-f]{64}$'",
    )
    for column in (
        "skill_name",
        "hints_json",
        "rubric_json",
        "source_ids_json",
        "hints_requested",
        "required",
    ):
        op.alter_column("learning_activities", column, server_default=None)

    op.drop_constraint(op.f("ck_learning_attempts_score_allowed"), "learning_attempts", type_="check")
    op.drop_constraint(op.f("ck_learning_attempts_result_consistent"), "learning_attempts", type_="check")
    op.add_column("learning_attempts", sa.Column("mistake_code", sa.String(length=64), nullable=True))
    op.create_check_constraint(op.f("ck_learning_attempts_score_allowed"), "learning_attempts", "score_bps BETWEEN 0 AND 10000")
    op.create_check_constraint(
        op.f("ck_learning_attempts_result_consistent"),
        "learning_attempts",
        "(is_correct AND score_bps >= 7000) OR (NOT is_correct AND score_bps < 7000)",
    )
    op.create_check_constraint(
        op.f("ck_learning_attempts_mistake_code_safe"),
        "learning_attempts",
        "mistake_code IS NULL OR mistake_code ~ '^[a-z0-9_]{1,64}$'",
    )

    op.create_table(
        "learning_skills",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("program_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("mastery_bps", sa.SmallInteger(), nullable=False),
        sa.Column("confidence_bps", sa.SmallInteger(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("mistake_count", sa.Integer(), nullable=False),
        sa.Column("last_score_bps", sa.SmallInteger(), nullable=False),
        sa.Column("last_practiced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_review_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("char_length(trim(name)) BETWEEN 1 AND 160", name=op.f("ck_learning_skills_name_bounded_nonblank")),
        sa.CheckConstraint("mastery_bps BETWEEN 0 AND 10000", name=op.f("ck_learning_skills_mastery_bounded")),
        sa.CheckConstraint("confidence_bps BETWEEN 0 AND 10000", name=op.f("ck_learning_skills_confidence_bounded")),
        sa.CheckConstraint("attempts BETWEEN 0 AND 1000000", name=op.f("ck_learning_skills_attempts_bounded")),
        sa.CheckConstraint("last_score_bps BETWEEN 0 AND 10000", name=op.f("ck_learning_skills_last_score_bounded")),
        sa.CheckConstraint("mistake_count BETWEEN 0 AND attempts", name=op.f("ck_learning_skills_mistakes_bounded")),
        sa.ForeignKeyConstraint(["program_id", "owner_id"], ["learning_programs.id", "learning_programs.owner_id"], name="fk_learning_skills_program_owner", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_learning_skills")),
        sa.UniqueConstraint("program_id", "name", name="uq_learning_skills_program_name"),
        sa.UniqueConstraint("id", "program_id", "owner_id", name="uq_learning_skills_identity"),
    )
    op.create_index("ix_learning_skills_program_mastery", "learning_skills", ["program_id", "mastery_bps"], unique=False)

    op.create_table(
        "learning_sources",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("program_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("document_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("asset_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("char_length(trim(label)) BETWEEN 1 AND 255", name=op.f("ck_learning_sources_label_bounded_nonblank")),
        sa.CheckConstraint("source_sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_learning_sources_source_sha256_valid")),
        sa.ForeignKeyConstraint(["program_id", "owner_id"], ["learning_programs.id", "learning_programs.owner_id"], name="fk_learning_sources_program_owner", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id", "owner_id"], ["documents.id", "documents.owner_id"], name="fk_learning_sources_document_owner", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["asset_id", "owner_id"], ["assets.id", "assets.owner_id"], name="fk_learning_sources_asset_owner", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_learning_sources")),
        sa.UniqueConstraint("program_id", "document_id", name="uq_learning_sources_program_document"),
        sa.UniqueConstraint("id", "program_id", "owner_id", name="uq_learning_sources_identity"),
    )

    op.create_table(
        "learning_sessions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("program_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("current_lesson_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("mode", _enum(*TEACHING_MODES, name="learning_session_mode"), nullable=False),
        sa.Column("status", _enum("active", "paused", "completed", name="learning_session_status"), nullable=False),
        sa.Column("focus", sa.String(length=500), nullable=False),
        sa.Column("planned_minutes", sa.SmallInteger(), nullable=False),
        sa.Column("interruption_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('active', 'paused', 'completed')", name=op.f("ck_learning_sessions_status_allowed")),
        sa.CheckConstraint("mode IN ('teacher', 'socratic', 'coach', 'mentor', 'interviewer', 'pair_programming', 'study', 'focus', 'exam', 'revision')", name=op.f("ck_learning_sessions_mode_allowed")),
        sa.CheckConstraint("char_length(trim(focus)) BETWEEN 1 AND 500", name=op.f("ck_learning_sessions_focus_bounded_nonblank")),
        sa.CheckConstraint("planned_minutes BETWEEN 5 AND 480", name=op.f("ck_learning_sessions_planned_minutes_bounded")),
        sa.CheckConstraint("interruption_count BETWEEN 0 AND 10000", name=op.f("ck_learning_sessions_interruptions_bounded")),
        sa.CheckConstraint("(status = 'active' AND paused_at IS NULL AND completed_at IS NULL) OR (status = 'paused' AND paused_at IS NOT NULL AND completed_at IS NULL) OR (status = 'completed' AND completed_at IS NOT NULL)", name=op.f("ck_learning_sessions_lifecycle_consistent")),
        sa.ForeignKeyConstraint(["program_id", "owner_id"], ["learning_programs.id", "learning_programs.owner_id"], name="fk_learning_sessions_program_owner", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["current_lesson_id", "program_id", "owner_id"], ["learning_lessons.id", "learning_lessons.program_id", "learning_lessons.owner_id"], name="fk_learning_sessions_lesson_program_owner", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_learning_sessions")),
        sa.UniqueConstraint("id", "program_id", "owner_id", name="uq_learning_sessions_identity"),
    )
    op.create_index("ix_learning_sessions_program_started_at", "learning_sessions", ["program_id", sa.text("started_at DESC")], unique=False)
    op.create_index("uq_learning_sessions_open_program", "learning_sessions", ["program_id"], unique=True, postgresql_where=sa.text("status IN ('active', 'paused')"))

    op.create_table(
        "learning_events",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("program_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("entity_kind", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("metadata_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("action ~ '^[a-z][a-z0-9_]{1,63}$'", name=op.f("ck_learning_events_action_safe")),
        sa.CheckConstraint("entity_kind ~ '^[a-z][a-z0-9_]{1,31}$'", name=op.f("ck_learning_events_entity_kind_safe")),
        sa.CheckConstraint("metadata_sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_learning_events_metadata_sha256_valid")),
        sa.ForeignKeyConstraint(["program_id", "owner_id"], ["learning_programs.id", "learning_programs.owner_id"], name="fk_learning_events_program_owner", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_learning_events")),
    )
    op.create_index("ix_learning_events_program_created_at", "learning_events", ["program_id", sa.text("created_at DESC")], unique=False)


def downgrade() -> None:
    op.drop_index("ix_learning_events_program_created_at", table_name="learning_events")
    op.drop_table("learning_events")
    op.drop_index("uq_learning_sessions_open_program", table_name="learning_sessions")
    op.drop_index("ix_learning_sessions_program_started_at", table_name="learning_sessions")
    op.drop_table("learning_sessions")
    op.drop_table("learning_sources")
    op.drop_index("ix_learning_skills_program_mastery", table_name="learning_skills")
    op.drop_table("learning_skills")

    op.drop_constraint(op.f("ck_learning_attempts_mistake_code_safe"), "learning_attempts", type_="check")
    op.drop_constraint(op.f("ck_learning_attempts_result_consistent"), "learning_attempts", type_="check")
    op.drop_constraint(op.f("ck_learning_attempts_score_allowed"), "learning_attempts", type_="check")
    op.drop_column("learning_attempts", "mistake_code")
    op.execute(
        "UPDATE learning_attempts SET score_bps = CASE WHEN is_correct THEN 10000 ELSE 0 END"
    )
    op.create_check_constraint(op.f("ck_learning_attempts_score_allowed"), "learning_attempts", "score_bps IN (0, 10000)")
    op.create_check_constraint(op.f("ck_learning_attempts_result_consistent"), "learning_attempts", "(is_correct AND score_bps = 10000) OR (NOT is_correct AND score_bps = 0)")

    for name in (
        "generation_sha256_valid",
        "hints_requested_bounded",
        "source_ids_json_bounded",
        "rubric_json_bounded",
        "hints_json_bounded",
        "skill_name_bounded_nonblank",
        "grading_mode_allowed",
    ):
        op.drop_constraint(op.f(f"ck_learning_activities_{name}"), "learning_activities", type_="check")
    for column in (
        "model_id",
        "generation_sha256",
        "required",
        "hints_requested",
        "source_ids_json",
        "rubric_json",
        "hints_json",
        "skill_name",
        "grading_mode",
    ):
        op.drop_column("learning_activities", column)
    op.drop_constraint(op.f("ck_learning_activities_kind_allowed"), "learning_activities", type_="check")
    op.execute(
        "UPDATE learning_activities SET kind = 'quiz' "
        "WHERE kind IN ('mcq', 'short_answer', 'long_answer', 'coding', 'assignment')"
    )
    op.create_check_constraint(op.f("ck_learning_activities_kind_allowed"), "learning_activities", "kind IN ('exercise', 'quiz', 'conversation', 'revision')")

    op.drop_constraint(op.f("ck_learning_lessons_source_ids_json_bounded"), "learning_lessons", type_="check")
    op.drop_column("learning_lessons", "source_ids_json")
    op.drop_constraint(op.f("ck_learning_programs_streak_bounded"), "learning_programs", type_="check")
    op.drop_constraint(op.f("ck_learning_programs_preferences_json_bounded"), "learning_programs", type_="check")
    op.drop_constraint(op.f("ck_learning_programs_teaching_mode_allowed"), "learning_programs", type_="check")
    for column in ("last_study_date", "best_streak_days", "current_streak_days", "preferences_json", "teaching_mode"):
        op.drop_column("learning_programs", column)
    op.drop_constraint(op.f("uq_documents_id_owner"), "documents", type_="unique")
