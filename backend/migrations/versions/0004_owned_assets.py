"""Add user-owned opaque assets and ordered message attachments.

Revision ID: 0004_owned_assets
Revises: 0003_bound_message_content
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_owned_assets"
down_revision: Union[str, None] = "0003_bound_message_content"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "assets",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=True),
        sa.Column("media_type", sa.String(length=255), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=128), nullable=False),
        sa.Column("upload_idempotency_key", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "byte_size > 0",
            name=op.f("ck_assets_byte_size_positive"),
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_assets_content_sha256_lowercase_hex"),
        ),
        sa.CheckConstraint(
            "storage_key ~ '^objects/[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{32}$'",
            name=op.f("ck_assets_storage_key_generated"),
        ),
        sa.CheckConstraint(
            "deleted_at IS NULL OR deleted_at >= created_at",
            name=op.f("ck_assets_deleted_at_not_before_created_at"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_assets_owner_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assets")),
        sa.UniqueConstraint("storage_key", name=op.f("uq_assets_storage_key")),
        sa.UniqueConstraint(
            "owner_id",
            "upload_idempotency_key",
            name=op.f("uq_assets_owner_upload_idempotency_key"),
        ),
    )
    op.create_index(
        "ix_assets_owner_created_at_id",
        "assets",
        [
            "owner_id",
            sa.literal_column("created_at").desc(),
            sa.literal_column("id").desc(),
        ],
        unique=False,
    )
    op.create_index(
        "ix_assets_deleted_at",
        "assets",
        ["deleted_at"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NOT NULL"),
    )

    op.create_table(
        "message_assets",
        sa.Column("message_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("asset_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "position >= 1",
            name=op.f("ck_message_assets_position_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["assets.id"],
            name=op.f("fk_message_assets_asset_id_assets"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            name=op.f("fk_message_assets_message_id_messages"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "message_id",
            "asset_id",
            name=op.f("pk_message_assets"),
        ),
        sa.UniqueConstraint(
            "message_id",
            "position",
            name=op.f("uq_message_assets_message_position"),
        ),
        sa.UniqueConstraint(
            "asset_id",
            name=op.f("uq_message_assets_asset_id"),
        ),
    )


def downgrade() -> None:
    op.drop_table("message_assets")
    op.drop_index("ix_assets_deleted_at", table_name="assets")
    op.drop_index("ix_assets_owner_created_at_id", table_name="assets")
    op.drop_table("assets")
