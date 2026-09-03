import app.models  # noqa: F401
from app.db.base import Base
from tests.db.test_creative_experiences_migration import _upgrade_through_learning
from tests.db.test_initial_domain_migration import (
    RecordingOperations,
    _load_revision,
    _revision_path,
    _table_signature,
)


REVISION_FILENAME = "0018_connector_activation.py"


def _upgrade_through_creative(operations: RecordingOperations) -> None:
    _upgrade_through_learning(operations)
    _load_revision(operations, "0017_creative_experiences.py").upgrade()


def test_connector_activation_revision_follows_creative_experiences():
    operations = RecordingOperations()
    revision = _load_revision(operations, REVISION_FILENAME)

    assert _revision_path(REVISION_FILENAME).name == REVISION_FILENAME
    assert revision.revision == "0018_connector_activation"
    assert revision.down_revision == "0017_creative_experiences"
    assert revision.branch_labels is None
    assert revision.depends_on is None


def test_upgrade_matches_exact_current_connector_orm_schema():
    operations = RecordingOperations()
    _upgrade_through_creative(operations)
    operations.events.clear()

    _load_revision(operations, REVISION_FILENAME).upgrade()

    for table_name in ("connectors", "connector_executions"):
        assert _table_signature(operations.metadata.tables[table_name]) == (
            _table_signature(Base.metadata.tables[table_name])
        )
    assert operations.events[:9] == [
        ("add_column", "connectors.provider"),
        ("add_column", "connectors.service"),
        ("add_column", "connectors.capabilities_json"),
        ("add_column", "connectors.discovery_path"),
        ("add_column", "connectors.last_successful_test_at"),
        ("add_column", "connectors.last_audit_reference"),
        ("alter_column", "connectors.provider"),
        ("alter_column", "connectors.service"),
        ("alter_column", "connectors.capabilities_json"),
    ]


def test_downgrade_removes_activation_metadata_after_safe_normalization():
    operations = RecordingOperations()
    _upgrade_through_creative(operations)
    revision = _load_revision(operations, REVISION_FILENAME)
    revision.upgrade()
    operations.events.clear()

    revision.downgrade()

    assert operations.events[0:3] == [
        (
            "execute",
            "DELETE FROM connector_executions WHERE action = 'discover' OR error_code = 'connector_circuit_open'",
        ),
        ("execute", "UPDATE connectors SET kind = 'rest' WHERE kind = 'graphql'"),
        (
            "execute",
            "UPDATE connectors SET auth_kind = 'oauth2_bearer' WHERE auth_kind = 'oidc_bearer'",
        ),
    ]
    assert operations.events[-6:] == [
        ("drop_column", "connectors.last_audit_reference"),
        ("drop_column", "connectors.last_successful_test_at"),
        ("drop_column", "connectors.discovery_path"),
        ("drop_column", "connectors.capabilities_json"),
        ("drop_column", "connectors.service"),
        ("drop_column", "connectors.provider"),
    ]
