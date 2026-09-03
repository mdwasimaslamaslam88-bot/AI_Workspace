import app.models  # noqa: F401
from app.db.base import Base
from tests.db.test_connector_lifecycle_audit_migration import (
    _upgrade_through_connector_activation,
)
from tests.db.test_initial_domain_migration import (
    RecordingOperations,
    _load_revision,
    _revision_path,
    _table_signature,
)


REVISION_FILENAME = "0020_trading_safety.py"


def _upgrade_through_connector_lifecycle(operations: RecordingOperations) -> None:
    _upgrade_through_connector_activation(operations)
    _load_revision(operations, "0019_connector_lifecycle_audit.py").upgrade()


def test_trading_safety_revision_follows_connector_lifecycle():
    operations = RecordingOperations()
    revision = _load_revision(operations, REVISION_FILENAME)

    assert _revision_path(REVISION_FILENAME).name == REVISION_FILENAME
    assert revision.revision == "0020_trading_safety"
    assert revision.down_revision == "0019_connector_lifecycle_audit"
    assert revision.branch_labels is None
    assert revision.depends_on is None


def test_upgrade_matches_exact_trading_safety_orm_schema():
    operations = RecordingOperations()
    _upgrade_through_connector_lifecycle(operations)
    operations.events.clear()

    _load_revision(operations, REVISION_FILENAME).upgrade()

    assert operations.events == [
        ("create_table", "trading_safety_policies"),
        ("create_table", "broker_order_records"),
        ("create_table", "trading_safety_events"),
        ("create_index", "ix_trading_safety_events_workspace_created_at"),
        ("create_index", "ix_broker_order_records_workspace_created_at"),
    ]
    for table_name in (
        "trading_safety_policies",
        "broker_order_records",
        "trading_safety_events",
    ):
        assert _table_signature(operations.metadata.tables[table_name]) == (
            _table_signature(Base.metadata.tables[table_name])
        )


def test_downgrade_removes_trading_safety_relations_in_dependency_order():
    operations = RecordingOperations()
    _upgrade_through_connector_lifecycle(operations)
    revision = _load_revision(operations, REVISION_FILENAME)
    revision.upgrade()
    operations.events.clear()

    revision.downgrade()

    assert operations.events == [
        ("drop_index", "ix_broker_order_records_workspace_created_at"),
        ("drop_index", "ix_trading_safety_events_workspace_created_at"),
        ("drop_table", "trading_safety_events"),
        ("drop_table", "broker_order_records"),
        ("drop_table", "trading_safety_policies"),
    ]
