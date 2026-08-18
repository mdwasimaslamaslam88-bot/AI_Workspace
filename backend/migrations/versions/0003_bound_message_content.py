"""Bound persisted Message content length.

Revision ID: 0003_bound_message_content
Revises: 0002_user_access_credential
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0003_bound_message_content"
down_revision: Union[str, None] = "0002_user_access_credential"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_check_constraint(
        op.f("ck_messages_content_length_bounded"),
        "messages",
        "char_length(content) <= 100000",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_messages_content_length_bounded"),
        "messages",
        type_="check",
    )
