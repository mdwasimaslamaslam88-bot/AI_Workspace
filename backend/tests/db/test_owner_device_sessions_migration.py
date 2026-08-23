import app.models  # noqa: F401
from app.db.base import Base
from tests.db.test_conversation_organization_migration import (
    _upgrade_predecessors,
)
from tests.db.test_initial_domain_migration import (
    RecordingOperations,
    _load_revision,
    _revision_path,
    _table_signature,
)


REVISION_FILENAME = "0012_owner_device_sessions.py"


def _upgrade_through_organization(operations: RecordingOperations) -> None:
    _upgrade_predecessors(operations)
    _load_revision(operations, "0011_conversation_organization.py").upgrade()


def test_owner_device_sessions_revision_extends_single_chain():
    revision = _load_revision(RecordingOperations(), REVISION_FILENAME)

    assert _revision_path(REVISION_FILENAME).name == REVISION_FILENAME
    assert revision.revision == "0012_owner_device_sessions"
    assert revision.down_revision == "0011_conversation_organization"
    assert revision.branch_labels is None
    assert revision.depends_on is None


def test_owner_device_sessions_upgrade_matches_current_schema_and_migrates_digest():
    operations = RecordingOperations()
    _upgrade_through_organization(operations)
    operations.events.clear()

    _load_revision(operations, REVISION_FILENAME).upgrade()

    assert operations.events == [
        ("create_table", "user_sessions"),
        ("create_index", "ix_user_sessions_user_revoked_created_at_id"),
        ("execute", "INSERT INTO user_sessions (id, user_id, access_token_digest, label, created_at, updated_at) SELECT CAST(md5(id::text || ':' || access_token_digest) AS uuid), id, access_token_digest, 'Migrated owner session', created_at, updated_at FROM users WHERE access_token_digest IS NOT NULL"),
        ("execute", "UPDATE users SET access_token_digest = NULL WHERE access_token_digest IS NOT NULL"),
    ]
    assert _table_signature(operations.metadata.tables["user_sessions"]) == (
        _table_signature(Base.metadata.tables["user_sessions"])
    )


def test_owner_device_sessions_downgrade_restores_one_digest_and_drops_table():
    operations = RecordingOperations()
    _upgrade_through_organization(operations)
    revision = _load_revision(operations, REVISION_FILENAME)
    revision.upgrade()
    operations.events.clear()

    revision.downgrade()

    assert operations.events[0][0] == "execute"
    assert "UPDATE users AS target SET access_token_digest" in operations.events[0][1]
    assert "WHERE revoked_at IS NULL" in operations.events[0][1]
    assert operations.events[1:] == [
        ("drop_index", "ix_user_sessions_user_revoked_created_at_id"),
        ("drop_table", "user_sessions"),
    ]
