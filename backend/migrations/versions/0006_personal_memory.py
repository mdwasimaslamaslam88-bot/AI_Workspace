"""Add explicit owner-scoped long-term personal memory.

Revision ID: 0006_personal_memory
Revises: 0005_document_intelligence
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006_personal_memory"
down_revision: Union[str, None] = "0005_document_intelligence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "memory_settings",
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
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
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_memory_settings_owner_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("owner_id", name=op.f("pk_memory_settings")),
    )
    op.create_table(
        "memories",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "category",
            sa.Enum(
                "preference",
                "fact",
                "instruction",
                "project_context",
                name="memory_category",
                native_enum=False,
                create_constraint=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("embedding", sa.LargeBinary(), nullable=True),
        sa.Column("embedding_norm", sa.Float(), nullable=True),
        sa.Column("provenance_kind", sa.String(length=32), nullable=False),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "category IN ('preference', 'fact', 'instruction', 'project_context')",
            name=op.f("ck_memories_category_allowed"),
        ),
        sa.CheckConstraint(
            "provenance_kind = 'explicit_user_entry'",
            name=op.f("ck_memories_provenance_kind_allowed"),
        ),
        sa.CheckConstraint(
            "content IS NULL OR (char_length(content) BETWEEN 1 AND 2000 "
            "AND char_length(btrim(content)) > 0)",
            name=op.f("ck_memories_content_bounded_non_blank"),
        ),
        sa.CheckConstraint(
            "(deleted_at IS NULL AND content IS NOT NULL "
            "AND octet_length(embedding) = 1024 AND embedding_norm > 0) OR "
            "(deleted_at IS NOT NULL AND content IS NULL "
            "AND embedding IS NULL AND embedding_norm IS NULL)",
            name=op.f("ck_memories_active_content_embedding_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_memories_owner_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memories")),
    )
    op.create_index(
        "ix_memories_owner_updated_at",
        "memories",
        ["owner_id", sa.literal_column("updated_at").desc()],
        unique=False,
    )
    op.create_index(
        "ix_memories_owner_category",
        "memories",
        ["owner_id", "category"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_memories_owner_category", table_name="memories")
    op.drop_index("ix_memories_owner_updated_at", table_name="memories")
    op.drop_table("memories")
    op.drop_table("memory_settings")
