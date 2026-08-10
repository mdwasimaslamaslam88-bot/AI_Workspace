"""Add the user access credential digest.

Revision ID: 0002_user_access_credential
Revises: 0001_initial_domain
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_user_access_credential"
down_revision: Union[str, None] = "0001_initial_domain"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "access_token_digest",
            sa.String(length=64),
            nullable=True,
        ),
    )
    op.create_unique_constraint(
        op.f("uq_users_access_token_digest"),
        "users",
        ["access_token_digest"],
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("uq_users_access_token_digest"),
        "users",
        type_="unique",
    )
    op.drop_column("users", "access_token_digest")
