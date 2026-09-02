import app.models  # noqa: F401
from app.db.base import Base
from tests.db.test_initial_domain_migration import (
    RecordingOperations,
    _load_revision,
    _revision_path,
    _table_signature,
)


REVISION_FILENAME = "0013_owner_connectors.py"


def _upgrade_predecessors(operations: RecordingOperations) -> None:
    for filename in (
        "0001_initial_domain.py",
        "0002_user_access_credential.py",
        "0003_bound_message_content.py",
        "0004_owned_assets.py",
        "0005_document_intelligence.py",
        "0006_personal_memory.py",
        "0007_bounded_tools.py",
        "0008_bounded_workflows.py",
        "0009_model_agnostic_document_embeddings.py",
        "0010_asset_provenance.py",
        "0011_conversation_organization.py",
        "0012_owner_device_sessions.py",
    ):
        _load_revision(operations, filename).upgrade()


def test_owner_connectors_revision_follows_device_sessions():
    operations = RecordingOperations()
    revision = _load_revision(operations, REVISION_FILENAME)

    assert _revision_path(REVISION_FILENAME).name == REVISION_FILENAME
    assert revision.revision == "0013_owner_connectors"
    assert revision.down_revision == "0012_owner_device_sessions"
    assert revision.branch_labels is None
    assert revision.depends_on is None


def test_upgrade_matches_exact_connector_orm_schema():
    operations = RecordingOperations()
    _upgrade_predecessors(operations)
    operations.events.clear()

    _load_revision(operations, REVISION_FILENAME).upgrade()

    assert operations.events == [
        ("create_table", "connectors"),
        ("create_table", "connector_executions"),
        ("create_index", "ix_connectors_owner_created_at"),
        ("create_index", "ix_connectors_owner_enabled"),
        ("create_index", "ix_connector_executions_owner_started_at"),
        ("create_index", "ix_connector_executions_connector_started_at"),
    ]
    for table_name in ("connectors", "connector_executions"):
        assert _table_signature(operations.metadata.tables[table_name]) == (
            _table_signature(Base.metadata.tables[table_name])
        )


def test_downgrade_removes_connector_relations_in_dependency_order():
    operations = RecordingOperations()
    _upgrade_predecessors(operations)
    revision = _load_revision(operations, REVISION_FILENAME)
    revision.upgrade()
    operations.events.clear()

    revision.downgrade()

    assert operations.events == [
        ("drop_index", "ix_connector_executions_connector_started_at"),
        ("drop_index", "ix_connector_executions_owner_started_at"),
        ("drop_index", "ix_connectors_owner_enabled"),
        ("drop_index", "ix_connectors_owner_created_at"),
        ("drop_table", "connector_executions"),
        ("drop_table", "connectors"),
    ]
