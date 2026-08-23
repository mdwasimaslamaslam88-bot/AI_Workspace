"""Add bounded owner device sessions.

Revision ID: 0012_owner_device_sessions
Revises: 0011_conversation_organization
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0012_owner_device_sessions"
down_revision: Union[str, None] = "0011_conversation_organization"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("access_token_digest", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=True),
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
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "access_token_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_user_sessions_access_token_digest_lowercase_hex"),
        ),
        sa.CheckConstraint(
            "label IS NULL OR (char_length(trim(label)) BETWEEN 1 AND 80)",
            name=op.f("ck_user_sessions_label_bounded_nonblank"),
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name=op.f("ck_user_sessions_revoked_at_not_before_created_at"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_sessions")),
        sa.UniqueConstraint(
            "access_token_digest",
            name="uq_user_sessions_access_token_digest",
        ),
    )
    op.create_index(
        "ix_user_sessions_user_revoked_created_at_id",
        "user_sessions",
        [
            "user_id",
            "revoked_at",
            sa.literal_column("created_at").desc(),
            sa.literal_column("id").desc(),
        ],
        unique=False,
    )
    op.execute(
        sa.text(
            "INSERT INTO user_sessions "
            "(id, user_id, access_token_digest, label, created_at, updated_at) "
            "SELECT CAST(md5(id::text || ':' || access_token_digest) AS uuid), "
            "id, access_token_digest, 'Migrated owner session', created_at, "
            "updated_at FROM users WHERE access_token_digest IS NOT NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE users SET access_token_digest = NULL "
            "WHERE access_token_digest IS NOT NULL"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE users AS target SET access_token_digest = source.digest "
            "FROM (SELECT DISTINCT ON (user_id) user_id, "
            "access_token_digest AS digest FROM user_sessions "
            "WHERE revoked_at IS NULL "
            "ORDER BY user_id, created_at DESC, id DESC) "
            "AS source WHERE target.id = source.user_id"
        )
    )
    op.drop_index(
        "ix_user_sessions_user_revoked_created_at_id",
        table_name="user_sessions",
    )
    op.drop_table("user_sessions")
