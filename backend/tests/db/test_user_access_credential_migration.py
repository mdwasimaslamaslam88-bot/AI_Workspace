import sqlalchemy as sa

import app.models  # noqa: F401  # Populate the model registry.
from app.db.base import Base
from tests.db.test_initial_domain_migration import (
    RecordingOperations,
    _column_signature,
    _conversation_signature_before_organization,
    _constraint_signature,
    _load_revision,
    _table_signature,
)


def _load_chain(operations: RecordingOperations):
    initial = _load_revision(operations)
    credential = _load_revision(
        operations,
        "0002_user_access_credential.py",
    )
    return initial, credential


def test_user_access_credential_revision_extends_the_single_revision_chain():
    operations = RecordingOperations()
    _initial, credential = _load_chain(operations)

    assert credential.revision == "0002_user_access_credential"
    assert credential.down_revision == "0001_initial_domain"
    assert credential.branch_labels is None
    assert credential.depends_on is None


def test_upgrade_adds_only_nullable_unique_digest_and_matches_metadata():
    operations = RecordingOperations()
    initial, credential = _load_chain(operations)
    initial.upgrade()
    initial_messages_signature = _table_signature(
        operations.metadata.tables["messages"]
    )
    operations.events.clear()

    credential.upgrade()

    assert operations.events == [
        ("add_column", "users.access_token_digest"),
        ("create_unique_constraint", "uq_users_access_token_digest"),
    ]
    assert set(operations.metadata.tables) == {"users", "conversations", "messages"}
    assert _table_signature(operations.metadata.tables["conversations"]) == (
        _conversation_signature_before_organization(
            Base.metadata.tables["conversations"]
        )
    )
    assert _table_signature(operations.metadata.tables["messages"]) == (
        initial_messages_signature
    )

    users = operations.metadata.tables["users"]
    model_users = Base.metadata.tables["users"]
    assert {
        column.name: _column_signature(column)
        for column in users.columns
    } == {
        column.name: _column_signature(column)
        for column in model_users.columns
    }
    assert {
        _constraint_signature(constraint)
        for constraint in users.constraints
    } == {
        _constraint_signature(constraint)
        for constraint in model_users.constraints
    }
    digest = users.c.access_token_digest
    assert isinstance(digest.type, sa.String)
    assert digest.type.length == 64
    assert digest.nullable is True
    unique = next(
        constraint
        for constraint in users.constraints
        if constraint.name == "uq_users_access_token_digest"
    )
    assert isinstance(unique, sa.UniqueConstraint)
    assert list(unique.columns.keys()) == ["access_token_digest"]


def test_downgrade_removes_only_the_user_access_credential_addition():
    operations = RecordingOperations()
    initial, credential = _load_chain(operations)
    initial.upgrade()
    initial_signatures = {
        name: _table_signature(table)
        for name, table in operations.metadata.tables.items()
    }
    credential.upgrade()
    operations.events.clear()

    credential.downgrade()

    assert operations.events == [
        ("drop_constraint", "uq_users_access_token_digest"),
        ("drop_column", "users.access_token_digest"),
    ]
    assert {
        name: _table_signature(table)
        for name, table in operations.metadata.tables.items()
    } == initial_signatures
