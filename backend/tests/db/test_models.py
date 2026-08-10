from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import configure_mappers
from sqlalchemy.schema import CreateIndex

import app.models  # noqa: F401  # Populate the model registry.
from app.db.base import Base
from app.models import Conversation, Message, MessageRole, User


EXPECTED_TABLES = {"users", "conversations", "messages"}
EXPECTED_MESSAGE_ROLES = ("system", "user", "assistant", "tool")


def _check_constraints(table) -> dict[str, str]:
    return {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }


def _assert_uuid_primary_key(table) -> None:
    identifier = table.c.id
    assert isinstance(identifier.type, Uuid)
    assert identifier.type.as_uuid is True
    assert identifier.type.python_type is UUID
    assert identifier.primary_key is True
    assert identifier.nullable is False
    assert identifier.default is not None
    assert identifier.default.is_callable
    assert identifier.server_default is None


def _assert_timestamps(table) -> None:
    for name in ("created_at", "updated_at"):
        column = table.c[name]
        assert isinstance(column.type, DateTime)
        assert column.type.timezone is True
        assert column.nullable is False
        assert column.server_default is not None

    assert table.c.updated_at.onupdate is not None


def test_model_registry_contains_only_the_approved_domain_tables():
    assert set(Base.metadata.tables) == EXPECTED_TABLES
    assert User.__table__ is Base.metadata.tables["users"]
    assert Conversation.__table__ is Base.metadata.tables["conversations"]
    assert Message.__table__ is Base.metadata.tables["messages"]


def test_user_has_uuid_primary_key_timestamps_and_access_credential_digest():
    table = User.__table__
    assert set(table.c.keys()) == {
        "id",
        "access_token_digest",
        "created_at",
        "updated_at",
    }

    _assert_uuid_primary_key(table)
    _assert_timestamps(table)

    access_token_digest = table.c.access_token_digest
    assert isinstance(access_token_digest.type, String)
    assert access_token_digest.type.length == 64
    assert access_token_digest.nullable is True

    unique_constraints = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    ]
    assert len(unique_constraints) == 1
    assert unique_constraints[0].name == "uq_users_access_token_digest"
    assert list(unique_constraints[0].columns.keys()) == [
        "access_token_digest"
    ]
    assert not table.indexes


def test_conversation_columns_constraints_and_owner_foreign_key():
    table = Conversation.__table__
    assert set(table.c.keys()) == {
        "id",
        "owner_id",
        "title",
        "next_message_sequence",
        "created_at",
        "updated_at",
    }
    _assert_uuid_primary_key(table)

    owner_id = table.c.owner_id
    assert isinstance(owner_id.type, Uuid)
    assert owner_id.nullable is False
    owner_fk = next(iter(owner_id.foreign_keys))
    assert owner_fk.target_fullname == "users.id"
    assert owner_fk.ondelete == "RESTRICT"

    title = table.c.title
    assert isinstance(title.type, String)
    assert title.type.length == 255
    assert title.nullable is True
    assert title.unique is not True

    next_sequence = table.c.next_message_sequence
    assert isinstance(next_sequence.type, BigInteger)
    assert next_sequence.nullable is False
    assert str(next_sequence.server_default.arg) == "1"

    checks = _check_constraints(table)
    assert checks["ck_conversations_title_non_blank"] == (
        "title IS NULL OR char_length(trim(title)) > 0"
    )
    assert checks["ck_conversations_next_message_sequence_positive"] == (
        "next_message_sequence >= 1"
    )
    assert not any(
        isinstance(constraint, UniqueConstraint)
        and "title" in constraint.columns.keys()
        for constraint in table.constraints
    )
    _assert_timestamps(table)


def test_conversation_owner_pagination_index_has_expected_order():
    table = Conversation.__table__
    index = next(
        candidate
        for candidate in table.indexes
        if candidate.name == "ix_conversations_owner_updated_at_id"
    )

    assert list(index.columns.keys()) == ["owner_id", "updated_at", "id"]
    ddl = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    assert ddl == (
        "CREATE INDEX ix_conversations_owner_updated_at_id "
        "ON conversations (owner_id, updated_at DESC, id DESC)"
    )


def test_message_columns_constraints_and_conversation_foreign_key():
    table = Message.__table__
    assert set(table.c.keys()) == {
        "id",
        "conversation_id",
        "role",
        "content",
        "sequence_number",
        "created_at",
        "updated_at",
    }
    assert "owner_id" not in table.c
    _assert_uuid_primary_key(table)

    conversation_id = table.c.conversation_id
    assert isinstance(conversation_id.type, Uuid)
    assert conversation_id.nullable is False
    conversation_fk = next(iter(conversation_id.foreign_keys))
    assert conversation_fk.target_fullname == "conversations.id"
    assert conversation_fk.ondelete == "CASCADE"

    role = table.c.role
    assert isinstance(role.type, Enum)
    assert isinstance(role.type, String)
    assert role.type.length == 32
    assert role.type.native_enum is False
    assert tuple(role.type.enums) == EXPECTED_MESSAGE_ROLES
    assert tuple(member.value for member in MessageRole) == EXPECTED_MESSAGE_ROLES
    assert role.nullable is False

    content = table.c.content
    assert isinstance(content.type, Text)
    assert content.nullable is False

    sequence = table.c.sequence_number
    assert isinstance(sequence.type, BigInteger)
    assert sequence.nullable is False

    checks = _check_constraints(table)
    assert checks["ck_messages_role_allowed"] == (
        "role IN ('system', 'user', 'assistant', 'tool')"
    )
    assert checks["ck_messages_sequence_number_positive"] == "sequence_number >= 1"
    assert all("content" not in sqltext for sqltext in checks.values())

    unique_constraints = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    ]
    assert len(unique_constraints) == 1
    assert unique_constraints[0].name == "uq_messages_conversation_sequence_number"
    assert list(unique_constraints[0].columns.keys()) == [
        "conversation_id",
        "sequence_number",
    ]
    _assert_timestamps(table)


def test_mapper_relationships_resolve_with_async_safe_loading():
    configure_mappers()

    user_conversations = User.__mapper__.relationships["conversations"]
    conversation_owner = Conversation.__mapper__.relationships["owner"]
    conversation_messages = Conversation.__mapper__.relationships["messages"]
    message_conversation = Message.__mapper__.relationships["conversation"]

    assert user_conversations.mapper.class_ is Conversation
    assert user_conversations.back_populates == "owner"
    assert user_conversations.lazy == "raise"

    assert conversation_owner.mapper.class_ is User
    assert conversation_owner.back_populates == "conversations"
    assert conversation_owner.lazy == "raise"

    assert conversation_messages.mapper.class_ is Message
    assert conversation_messages.back_populates == "conversation"
    assert conversation_messages.lazy == "raise"
    assert tuple(conversation_messages.order_by) == (
        Message.__table__.c.sequence_number,
    )

    assert message_conversation.mapper.class_ is Conversation
    assert message_conversation.back_populates == "messages"
    assert message_conversation.lazy == "raise"
    assert set(Base.metadata.tables) == EXPECTED_TABLES
