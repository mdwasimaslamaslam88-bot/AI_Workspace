"""Add model identity and dimensions to document embeddings.

Revision ID: 0009_document_embedding_models
Revises: 0008_bounded_workflows
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0009_document_embedding_models"
down_revision: Union[str, None] = "0008_bounded_workflows"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "document_chunks",
        sa.Column(
            "embedding_model",
            sa.String(length=255),
            nullable=False,
            server_default="local-hash-v1",
        ),
    )
    op.add_column(
        "document_chunks",
        sa.Column(
            "embedding_dimensions",
            sa.Integer(),
            nullable=False,
            server_default="256",
        ),
    )
    op.drop_constraint(
        op.f("ck_document_chunks_embedding_dimensions_fixed"),
        "document_chunks",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_document_chunks_embedding_dimensions_bounded"),
        "document_chunks",
        "embedding_dimensions BETWEEN 1 AND 4096",
    )
    op.create_check_constraint(
        op.f("ck_document_chunks_embedding_bytes_consistent"),
        "document_chunks",
        "octet_length(embedding) = embedding_dimensions * 4",
    )
    op.create_check_constraint(
        op.f("ck_document_chunks_embedding_model_safe"),
        "document_chunks",
        "embedding_model ~ '^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,254}$'",
    )
    op.alter_column("document_chunks", "embedding_model", server_default=None)
    op.alter_column("document_chunks", "embedding_dimensions", server_default=None)


def downgrade() -> None:
    incompatible = op.get_bind().execute(
        sa.text(
            "SELECT count(*) FROM document_chunks "
            "WHERE embedding_dimensions != 256 OR octet_length(embedding) != 1024"
        )
    ).scalar_one()
    if incompatible:
        raise RuntimeError(
            "cannot downgrade while non-legacy document embeddings exist"
        )
    op.drop_constraint(
        op.f("ck_document_chunks_embedding_model_safe"),
        "document_chunks",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_document_chunks_embedding_bytes_consistent"),
        "document_chunks",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_document_chunks_embedding_dimensions_bounded"),
        "document_chunks",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_document_chunks_embedding_dimensions_fixed"),
        "document_chunks",
        "octet_length(embedding) = 1024",
    )
    op.drop_column("document_chunks", "embedding_dimensions")
    op.drop_column("document_chunks", "embedding_model")
