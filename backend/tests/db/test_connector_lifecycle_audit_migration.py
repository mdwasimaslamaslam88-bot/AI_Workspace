import app.models  # noqa: F401
from app.db.base import Base
from tests.db.test_connector_activation_migration import _upgrade_through_creative
from tests.db.test_initial_domain_migration import (
    RecordingOperations,
    _load_revision,
    _revision_path,
    _table_signature,
)


REVISION_FILENAME = "0019_connector_lifecycle_audit.py"


def _upgrade_through_connector_activation(operations: RecordingOperations) -> None:
    _upgrade_through_creative(operations)
    _load_revision(operations, "0018_connector_activation.py").upgrade()


def test_connector_lifecycle_audit_revision_follows_connector_activation():
    operations = RecordingOperations()
    revision = _load_revision(operations, REVISION_FILENAME)

    assert _revision_path(REVISION_FILENAME).name == REVISION_FILENAME
    assert revision.revision == "0019_connector_lifecycle_audit"
    assert revision.down_revision == "0018_connector_activation"
    assert revision.branch_labels is None
    assert revision.depends_on is None


def test_upgrade_matches_exact_current_connector_audit_schema():
    operations = RecordingOperations()
    _upgrade_through_connector_activation(operations)
    operations.events.clear()

    _load_revision(operations, REVISION_FILENAME).upgrade()

    assert _table_signature(operations.metadata.tables["connector_executions"]) == (
        _table_signature(Base.metadata.tables["connector_executions"])
    )
    assert operations.events == [
        ("drop_constraint", "ck_connector_executions_action_allowed"),
        ("create_check_constraint", "ck_connector_executions_action_allowed"),
    ]


def test_downgrade_removes_lifecycle_records_before_restoring_constraint():
    operations = RecordingOperations()
    _upgrade_through_connector_activation(operations)
    revision = _load_revision(operations, REVISION_FILENAME)
    revision.upgrade()
    operations.events.clear()

    revision.downgrade()

    assert operations.events == [
        (
            "execute",
            "DELETE FROM connector_executions WHERE action IN ('configure', "
            "'credential_change', 'permission_change', 'authenticate', 'activate', "
            "'disconnect', 'reconnect', 'revoke')",
        ),
        ("drop_constraint", "ck_connector_executions_action_allowed"),
        ("create_check_constraint", "ck_connector_executions_action_allowed"),
    ]
