"""Add explicit provenance for locally generated media assets.

Revision ID: 0010_asset_provenance
Revises: 0009_document_embedding_models
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010_asset_provenance"
down_revision: Union[str, None] = "0009_document_embedding_models"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "assets",
        sa.Column(
            "provenance_kind",
            sa.String(length=32),
            nullable=False,
            server_default="upload",
        ),
    )
    op.add_column(
        "assets",
        sa.Column("source_asset_id", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.add_column(
        "assets",
        sa.Column("runtime_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "assets",
        sa.Column("model_id", sa.String(length=96), nullable=True),
    )
    op.create_unique_constraint(
        op.f("uq_assets_id_owner_id"),
        "assets",
        ["id", "owner_id"],
    )
    op.create_foreign_key(
        op.f("fk_assets_source_asset_id_assets"),
        "assets",
        "assets",
        ["source_asset_id", "owner_id"],
        ["id", "owner_id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_assets_provenance_kind_known"),
        "assets",
        "provenance_kind IN ('upload', 'image_generation', "
        "'image_editing', 'speech_synthesis')",
    )
    op.create_check_constraint(
        op.f("ck_assets_runtime_id_safe"),
        "assets",
        "runtime_id IS NULL OR runtime_id ~ '^[a-z0-9][a-z0-9_-]{0,63}$'",
    )
    op.create_check_constraint(
        op.f("ck_assets_model_id_public"),
        "assets",
        "model_id IS NULL OR model_id ~ "
        "'^[a-z0-9][a-z0-9_-]{0,63}:[a-f0-9]{24}$'",
    )
    op.create_check_constraint(
        op.f("ck_assets_provenance_consistent"),
        "assets",
        "(provenance_kind = 'upload' AND source_asset_id IS NULL "
        "AND runtime_id IS NULL AND model_id IS NULL) OR "
        "(provenance_kind IN ('image_generation', 'speech_synthesis') "
        "AND source_asset_id IS NULL AND runtime_id IS NOT NULL "
        "AND model_id IS NOT NULL) OR "
        "(provenance_kind = 'image_editing' AND source_asset_id IS NOT NULL "
        "AND runtime_id IS NOT NULL AND model_id IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_assets_source_not_self"),
        "assets",
        "source_asset_id IS NULL OR source_asset_id <> id",
    )
    op.create_index(
        "ix_assets_source_asset_id",
        "assets",
        ["source_asset_id"],
        unique=False,
        postgresql_where=sa.text("source_asset_id IS NOT NULL"),
    )
    op.alter_column("assets", "provenance_kind", server_default=None)


def downgrade() -> None:
    generated_count = op.get_bind().execute(
        sa.text("SELECT count(*) FROM assets WHERE provenance_kind <> 'upload'")
    ).scalar_one()
    if generated_count:
        raise RuntimeError("cannot downgrade while generated media assets exist")
    op.drop_index("ix_assets_source_asset_id", table_name="assets")
    op.drop_constraint(
        op.f("ck_assets_source_not_self"), "assets", type_="check"
    )
    op.drop_constraint(
        op.f("ck_assets_provenance_consistent"), "assets", type_="check"
    )
    op.drop_constraint(
        op.f("ck_assets_model_id_public"), "assets", type_="check"
    )
    op.drop_constraint(
        op.f("ck_assets_runtime_id_safe"), "assets", type_="check"
    )
    op.drop_constraint(
        op.f("ck_assets_provenance_kind_known"), "assets", type_="check"
    )
    op.drop_constraint(
        op.f("fk_assets_source_asset_id_assets"), "assets", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("uq_assets_id_owner_id"), "assets", type_="unique"
    )
    op.drop_column("assets", "model_id")
    op.drop_column("assets", "runtime_id")
    op.drop_column("assets", "source_asset_id")
    op.drop_column("assets", "provenance_kind")
