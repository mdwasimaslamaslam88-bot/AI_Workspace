"""Add owner conversation organization state.

Revision ID: 0011_conversation_organization
Revises: 0010_asset_provenance
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0011_conversation_organization"
down_revision: Union[str, None] = "0010_asset_provenance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column(
            "is_pinned",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "is_archived",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "ix_conversations_owner_archived_updated_at_id",
        "conversations",
        [
            "owner_id",
            "is_archived",
            sa.literal_column("updated_at").desc(),
            sa.literal_column("id").desc(),
        ],
        unique=False,
    )
    op.alter_column("conversations", "is_pinned", server_default=None)
    op.alter_column("conversations", "is_archived", server_default=None)


def downgrade() -> None:
    op.drop_index(
        "ix_conversations_owner_archived_updated_at_id",
        table_name="conversations",
    )
    op.drop_column("conversations", "is_archived")
    op.drop_column("conversations", "is_pinned")
