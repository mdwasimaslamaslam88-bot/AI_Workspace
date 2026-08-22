"""Add owner-scoped document ingestion, chunks, embeddings, and citations.

Revision ID: 0005_document_intelligence
Revises: 0004_owned_assets
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_document_intelligence"
down_revision: Union[str, None] = "0004_owned_assets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("asset_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "processing", "ready", "failed", "cancelled",
                name="document_status",
                native_enum=False,
                create_constraint=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("ingestion_token", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("character_count", sa.BigInteger(), nullable=False),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
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
            "chunk_count >= 0",
            name=op.f("ck_documents_chunk_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "character_count >= 0",
            name=op.f("ck_documents_character_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "failure_code IS NULL OR failure_code ~ '^[a-z0-9_]{1,64}$'",
            name=op.f("ck_documents_failure_code_safe"),
        ),
        sa.CheckConstraint(
            "(status = 'processing') = (ingestion_token IS NOT NULL)",
            name=op.f("ck_documents_processing_token_consistent"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'failed', 'cancelled')",
            name=op.f("ck_documents_status_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["assets.id"],
            name=op.f("fk_documents_asset_id_assets"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_documents_owner_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        sa.UniqueConstraint("asset_id", name=op.f("uq_documents_asset_id")),
    )
    op.create_index(
        "ix_documents_owner_status",
        "documents",
        ["owner_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_documents_owner_updated_at",
        "documents",
        ["owner_id", sa.literal_column("updated_at").desc()],
        unique=False,
    )

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("document_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("asset_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", sa.LargeBinary(), nullable=False),
        sa.Column("embedding_norm", sa.Float(), nullable=False),
        sa.Column("provenance_kind", sa.String(length=16), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("row_start", sa.Integer(), nullable=True),
        sa.Column("row_end", sa.Integer(), nullable=True),
        sa.Column("section", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "ordinal >= 1",
            name=op.f("ck_document_chunks_ordinal_positive"),
        ),
        sa.CheckConstraint(
            "char_length(content) BETWEEN 1 AND 1600",
            name=op.f("ck_document_chunks_content_length_bounded"),
        ),
        sa.CheckConstraint(
            "octet_length(embedding) = 1024",
            name=op.f("ck_document_chunks_embedding_dimensions_fixed"),
        ),
        sa.CheckConstraint(
            "embedding_norm > 0",
            name=op.f("ck_document_chunks_embedding_norm_positive"),
        ),
        sa.CheckConstraint(
            "page_number IS NULL OR page_number >= 1",
            name=op.f("ck_document_chunks_page_number_positive"),
        ),
        sa.CheckConstraint(
            "row_start IS NULL OR row_start >= 1",
            name=op.f("ck_document_chunks_row_start_positive"),
        ),
        sa.CheckConstraint(
            "row_end IS NULL OR row_end >= row_start",
            name=op.f("ck_document_chunks_row_range_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["assets.id"],
            name=op.f("fk_document_chunks_asset_id_assets"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_document_chunks_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_document_chunks_owner_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_chunks")),
        sa.UniqueConstraint(
            "document_id",
            "ordinal",
            name=op.f("uq_document_chunks_ordinal"),
        ),
    )
    op.create_index(
        "ix_document_chunks_owner_document_ordinal",
        "document_chunks",
        ["owner_id", "document_id", "ordinal"],
        unique=False,
    )

    op.create_table(
        "message_citations",
        sa.Column("message_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("document_chunk_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "position >= 1",
            name=op.f("ck_message_citations_position_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["document_chunk_id"],
            ["document_chunks.id"],
            name=op.f("fk_message_citations_document_chunk_id_document_chunks"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            name=op.f("fk_message_citations_message_id_messages"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "message_id",
            "document_chunk_id",
            name=op.f("pk_message_citations"),
        ),
        sa.UniqueConstraint(
            "message_id",
            "position",
            name=op.f("uq_message_citations_message_position"),
        ),
    )


def downgrade() -> None:
    op.drop_table("message_citations")
    op.drop_index(
        "ix_document_chunks_owner_document_ordinal",
        table_name="document_chunks",
    )
    op.drop_table("document_chunks")
    op.drop_index("ix_documents_owner_updated_at", table_name="documents")
    op.drop_index("ix_documents_owner_status", table_name="documents")
    op.drop_table("documents")
