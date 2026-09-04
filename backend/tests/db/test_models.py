from uuid import UUID

import pytest
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Integer,
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
from app.models import (
    AgentMission,
    AgentMissionEvent,
    Asset,
    BrokerOrderRecord,
    Connector,
    ConnectorExecution,
    Conversation,
    CreativeExperience,
    CreativeTurn,
    Document,
    DocumentChunk,
    FinanceArtifact,
    FinanceWorkspace,
    LearningActivity,
    LearningAttempt,
    LearningLesson,
    LearningProgram,
    LearningReviewItem,
    LearningSession,
    LearningSkill,
    LearningSource,
    LearningEvent,
    MarketAlert,
    MarketWatchItem,
    Message,
    MessageAsset,
    MessageCitation,
    Memory,
    MemorySetting,
    MarketingCampaign,
    MarketingStage,
    MessageRole,
    PaperOrder,
    PaperPosition,
    ToolExecution,
    TradingSafetyEvent,
    TradingSafetyPolicy,
    User,
    UserSession,
    Workflow,
    WorkflowStep,
)
from app.models.message import (
    MAX_MESSAGE_CONTENT_CHARACTERS,
    MessageContentTooLargeError,
    validate_message_content,
)


EXPECTED_TABLES = {
    "agent_missions",
    "agent_mission_events",
    "users",
    "user_sessions",
    "conversations",
    "messages",
    "assets",
    "connectors",
    "connector_executions",
    "message_assets",
    "documents",
    "document_chunks",
    "message_citations",
    "memory_settings",
    "memories",
    "tool_executions",
    "workflows",
    "workflow_steps",
    "marketing_campaigns",
    "marketing_stages",
    "finance_workspaces",
    "market_watch_items",
    "paper_positions",
    "paper_orders",
    "market_alerts",
    "finance_artifacts",
    "trading_safety_policies",
    "broker_order_records",
    "trading_safety_events",
    "learning_programs",
    "learning_lessons",
    "learning_activities",
    "learning_attempts",
    "learning_review_items",
    "learning_skills",
    "learning_sources",
    "learning_sessions",
    "learning_events",
    "creative_experiences",
    "creative_turns",
}
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
    assert UserSession.__table__ is Base.metadata.tables["user_sessions"]
    assert Conversation.__table__ is Base.metadata.tables["conversations"]
    assert Message.__table__ is Base.metadata.tables["messages"]
    assert Asset.__table__ is Base.metadata.tables["assets"]
    assert Connector.__table__ is Base.metadata.tables["connectors"]
    assert ConnectorExecution.__table__ is Base.metadata.tables["connector_executions"]
    assert MessageAsset.__table__ is Base.metadata.tables["message_assets"]
    assert Document.__table__ is Base.metadata.tables["documents"]
    assert DocumentChunk.__table__ is Base.metadata.tables["document_chunks"]
    assert MessageCitation.__table__ is Base.metadata.tables["message_citations"]
    assert MemorySetting.__table__ is Base.metadata.tables["memory_settings"]
    assert Memory.__table__ is Base.metadata.tables["memories"]
    assert ToolExecution.__table__ is Base.metadata.tables["tool_executions"]
    assert Workflow.__table__ is Base.metadata.tables["workflows"]
    assert WorkflowStep.__table__ is Base.metadata.tables["workflow_steps"]
    assert MarketingCampaign.__table__ is Base.metadata.tables["marketing_campaigns"]
    assert MarketingStage.__table__ is Base.metadata.tables["marketing_stages"]
    assert FinanceWorkspace.__table__ is Base.metadata.tables["finance_workspaces"]
    assert MarketWatchItem.__table__ is Base.metadata.tables["market_watch_items"]
    assert PaperPosition.__table__ is Base.metadata.tables["paper_positions"]
    assert PaperOrder.__table__ is Base.metadata.tables["paper_orders"]
    assert MarketAlert.__table__ is Base.metadata.tables["market_alerts"]
    assert FinanceArtifact.__table__ is Base.metadata.tables["finance_artifacts"]
    assert TradingSafetyPolicy.__table__ is Base.metadata.tables["trading_safety_policies"]
    assert BrokerOrderRecord.__table__ is Base.metadata.tables["broker_order_records"]
    assert TradingSafetyEvent.__table__ is Base.metadata.tables["trading_safety_events"]
    assert LearningProgram.__table__ is Base.metadata.tables["learning_programs"]
    assert LearningLesson.__table__ is Base.metadata.tables["learning_lessons"]
    assert LearningActivity.__table__ is Base.metadata.tables["learning_activities"]
    assert LearningAttempt.__table__ is Base.metadata.tables["learning_attempts"]
    assert LearningReviewItem.__table__ is Base.metadata.tables["learning_review_items"]
    assert LearningSkill.__table__ is Base.metadata.tables["learning_skills"]
    assert LearningSource.__table__ is Base.metadata.tables["learning_sources"]
    assert LearningSession.__table__ is Base.metadata.tables["learning_sessions"]
    assert LearningEvent.__table__ is Base.metadata.tables["learning_events"]
    assert CreativeExperience.__table__ is Base.metadata.tables["creative_experiences"]
    assert CreativeTurn.__table__ is Base.metadata.tables["creative_turns"]


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


def test_user_session_has_owner_scoped_bounded_digest_metadata():
    table = UserSession.__table__
    assert set(table.c.keys()) == {
        "id",
        "user_id",
        "access_token_digest",
        "label",
        "created_at",
        "updated_at",
        "revoked_at",
    }
    _assert_uuid_primary_key(table)
    _assert_timestamps(table)

    user_id = table.c.user_id
    assert isinstance(user_id.type, Uuid)
    assert user_id.nullable is False
    user_fk = next(iter(user_id.foreign_keys))
    assert user_fk.target_fullname == "users.id"
    assert user_fk.ondelete == "CASCADE"

    digest = table.c.access_token_digest
    assert isinstance(digest.type, String)
    assert digest.type.length == 64
    assert digest.nullable is False
    label = table.c.label
    assert isinstance(label.type, String)
    assert label.type.length == 80
    assert label.nullable is True
    assert table.c.revoked_at.type.timezone is True
    assert table.c.revoked_at.nullable is True

    checks = _check_constraints(table)
    assert checks == {
        "ck_user_sessions_access_token_digest_lowercase_hex": (
            "access_token_digest ~ '^[0-9a-f]{64}$'"
        ),
        "ck_user_sessions_label_bounded_nonblank": (
            "label IS NULL OR (char_length(trim(label)) BETWEEN 1 AND 80)"
        ),
        "ck_user_sessions_revoked_at_not_before_created_at": (
            "revoked_at IS NULL OR revoked_at >= created_at"
        ),
    }
    unique = next(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    )
    assert unique.name == "uq_user_sessions_access_token_digest"
    assert list(unique.columns.keys()) == ["access_token_digest"]

    index = next(iter(table.indexes))
    assert index.name == "ix_user_sessions_user_revoked_created_at_id"
    assert list(index.columns.keys()) == [
        "user_id",
        "revoked_at",
        "created_at",
        "id",
    ]
    ddl = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    assert ddl == (
        "CREATE INDEX ix_user_sessions_user_revoked_created_at_id ON "
        "user_sessions (user_id, revoked_at, created_at DESC, id DESC)"
    )


def test_conversation_columns_constraints_and_owner_foreign_key():
    table = Conversation.__table__
    assert set(table.c.keys()) == {
        "id",
        "owner_id",
        "title",
        "next_message_sequence",
        "is_pinned",
        "is_archived",
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

    for state_column_name in ("is_pinned", "is_archived"):
        state_column = table.c[state_column_name]
        assert isinstance(state_column.type, Boolean)
        assert state_column.nullable is False
        assert state_column.default is not None
        assert state_column.default.arg is False
        assert state_column.server_default is None

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

    organization_index = next(
        candidate
        for candidate in table.indexes
        if candidate.name == "ix_conversations_owner_archived_updated_at_id"
    )
    assert list(organization_index.columns.keys()) == [
        "owner_id",
        "is_archived",
        "updated_at",
        "id",
    ]
    organization_ddl = str(
        CreateIndex(organization_index).compile(dialect=postgresql.dialect())
    )
    assert organization_ddl == (
        "CREATE INDEX ix_conversations_owner_archived_updated_at_id "
        "ON conversations (owner_id, is_archived, updated_at DESC, id DESC)"
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
    assert checks["ck_messages_content_length_bounded"] == (
        "char_length(content) <= 100000"
    )
    assert set(checks) == {
        "ck_messages_role_allowed",
        "ck_messages_sequence_number_positive",
        "ck_messages_content_length_bounded",
    }

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
    user_sessions = User.__mapper__.relationships["sessions"]
    conversation_owner = Conversation.__mapper__.relationships["owner"]
    conversation_messages = Conversation.__mapper__.relationships["messages"]
    message_conversation = Message.__mapper__.relationships["conversation"]

    assert user_conversations.mapper.class_ is Conversation
    assert user_conversations.back_populates == "owner"
    assert user_conversations.lazy == "raise"
    assert user_sessions.mapper.class_ is UserSession
    assert user_sessions.back_populates == "user"
    assert user_sessions.lazy == "raise"

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


@pytest.mark.parametrize(
    "content",
    [
        "x" * MAX_MESSAGE_CONTENT_CHARACTERS,
        "é" * MAX_MESSAGE_CONTENT_CHARACTERS,
    ],
)
def test_message_content_validator_accepts_the_character_boundary(content):
    assert validate_message_content(content) is None


def test_message_content_validator_rejects_one_character_over_safely():
    fragment = "private-message-fragment"
    content = fragment + "x" * (
        MAX_MESSAGE_CONTENT_CHARACTERS + 1 - len(fragment)
    )

    with pytest.raises(MessageContentTooLargeError) as captured:
        validate_message_content(content)

    assert str(captured.value) == "persisted text is too large"
    assert fragment not in str(captured.value)
    assert str(MAX_MESSAGE_CONTENT_CHARACTERS) not in str(captured.value)
    assert "content" not in str(captured.value)


@pytest.mark.parametrize("content", [None, b"text", 1, True])
def test_message_content_validator_accepts_only_strings(content):
    with pytest.raises(TypeError, match="^value must be a string$"):
        validate_message_content(content)
