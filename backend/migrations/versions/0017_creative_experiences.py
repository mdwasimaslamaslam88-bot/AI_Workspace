"""Add persistent verified local creative experiences.

Revision ID: 0017_creative_experiences
Revises: 0016_learning_system
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0017_creative_experiences"
down_revision: Union[str, None] = "0016_learning_system"
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
        "creative_experiences",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "mode",
            _enum("story", "game", "character", name="creative_experience_mode"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("premise", sa.String(length=4000), nullable=False),
        sa.Column("genre", sa.String(length=80), nullable=False),
        sa.Column("language", sa.String(length=35), nullable=False),
        sa.Column("character_name", sa.String(length=120), nullable=True),
        sa.Column("safety_tier", sa.String(length=16), nullable=False),
        sa.Column(
            "status",
            _enum("active", "completed", "archived", name="creative_experience_status"),
            nullable=False,
        ),
        sa.Column("turn_count", sa.SmallInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "mode IN ('story', 'game', 'character')",
            name=op.f("ck_creative_experiences_mode_allowed"),
        ),
        sa.CheckConstraint(
            "char_length(trim(title)) BETWEEN 1 AND 160",
            name=op.f("ck_creative_experiences_title_bounded_nonblank"),
        ),
        sa.CheckConstraint(
            "char_length(trim(premise)) BETWEEN 1 AND 4000",
            name=op.f("ck_creative_experiences_premise_bounded_nonblank"),
        ),
        sa.CheckConstraint(
            "char_length(trim(genre)) BETWEEN 1 AND 80",
            name=op.f("ck_creative_experiences_genre_bounded_nonblank"),
        ),
        sa.CheckConstraint(
            "language ~ '^[A-Za-z][A-Za-z0-9-]{1,34}$'",
            name=op.f("ck_creative_experiences_language_valid"),
        ),
        sa.CheckConstraint(
            "character_name IS NULL OR char_length(trim(character_name)) BETWEEN 1 AND 120",
            name=op.f("ck_creative_experiences_character_name_bounded_nonblank"),
        ),
        sa.CheckConstraint(
            "(mode = 'character' AND character_name IS NOT NULL) OR mode <> 'character'",
            name=op.f("ck_creative_experiences_character_mode_named"),
        ),
        sa.CheckConstraint(
            "safety_tier = 'general'",
            name=op.f("ck_creative_experiences_safety_tier_general_only"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'completed', 'archived')",
            name=op.f("ck_creative_experiences_status_allowed"),
        ),
        sa.CheckConstraint(
            "turn_count BETWEEN 0 AND 100",
            name=op.f("ck_creative_experiences_turn_count_bounded"),
        ),
        sa.CheckConstraint(
            "(status = 'completed' AND completed_at IS NOT NULL) OR "
            "(status <> 'completed' AND completed_at IS NULL)",
            name=op.f("ck_creative_experiences_completion_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_creative_experiences_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_creative_experiences")),
        sa.UniqueConstraint(
            "id", "owner_id", name="uq_creative_experiences_id_owner"
        ),
    )
    op.create_table(
        "creative_turns",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("experience_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("owner_input", sa.String(length=4000), nullable=False),
        sa.Column("output", sa.Text(), nullable=False),
        sa.Column("output_sha256", sa.String(length=64), nullable=False),
        sa.Column("model_id", sa.String(length=96), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "position BETWEEN 1 AND 100",
            name=op.f("ck_creative_turns_position_bounded"),
        ),
        sa.CheckConstraint(
            "char_length(trim(owner_input)) BETWEEN 1 AND 4000",
            name=op.f("ck_creative_turns_owner_input_bounded_nonblank"),
        ),
        sa.CheckConstraint(
            "char_length(trim(output)) BETWEEN 1 AND 32768",
            name=op.f("ck_creative_turns_output_bounded_nonblank"),
        ),
        sa.CheckConstraint(
            "output_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_creative_turns_output_sha256_valid"),
        ),
        sa.CheckConstraint(
            "char_length(model_id) BETWEEN 1 AND 96",
            name=op.f("ck_creative_turns_model_id_bounded_nonblank"),
        ),
        sa.ForeignKeyConstraint(
            ["experience_id", "owner_id"],
            ["creative_experiences.id", "creative_experiences.owner_id"],
            name="fk_creative_turns_experience_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_creative_turns")),
        sa.UniqueConstraint(
            "experience_id", "position", name="uq_creative_turns_position"
        ),
    )
    op.create_index(
        "ix_creative_experiences_owner_updated_at",
        "creative_experiences",
        ["owner_id", sa.text("updated_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_creative_turns_experience_position",
        "creative_turns",
        ["experience_id", "position"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_creative_turns_experience_position", table_name="creative_turns"
    )
    op.drop_index(
        "ix_creative_experiences_owner_updated_at",
        table_name="creative_experiences",
    )
    op.drop_table("creative_turns")
    op.drop_table("creative_experiences")
