import app.models  # noqa: F401  # Populate the model registry.
from app.db.base import Base
from tests.db.test_initial_domain_migration import (
    RecordingOperations,
    _load_revision,
    _revision_path,
    _table_signature,
)


REVISION_FILENAME = "0006_personal_memory.py"


def _upgrade_predecessors(operations: RecordingOperations) -> None:
    for filename in (
        "0001_initial_domain.py",
        "0002_user_access_credential.py",
        "0003_bound_message_content.py",
        "0004_owned_assets.py",
        "0005_document_intelligence.py",
    ):
        _load_revision(operations, filename).upgrade()


def test_personal_memory_revision_follows_document_intelligence():
    operations = RecordingOperations()
    revision = _load_revision(operations, REVISION_FILENAME)

    assert _revision_path(REVISION_FILENAME).name == REVISION_FILENAME
    assert revision.revision == "0006_personal_memory"
    assert revision.down_revision == "0005_document_intelligence"
    assert revision.branch_labels is None
    assert revision.depends_on is None


def test_upgrade_matches_exact_memory_orm_schema():
    operations = RecordingOperations()
    _upgrade_predecessors(operations)
    operations.events.clear()

    _load_revision(operations, REVISION_FILENAME).upgrade()

    assert operations.events == [
        ("create_table", "memory_settings"),
        ("create_table", "memories"),
        ("create_index", "ix_memories_owner_updated_at"),
        ("create_index", "ix_memories_owner_category"),
    ]
    for table_name in ("memory_settings", "memories"):
        assert _table_signature(operations.metadata.tables[table_name]) == (
            _table_signature(Base.metadata.tables[table_name])
        )


def test_downgrade_removes_memory_relations_in_dependency_order():
    operations = RecordingOperations()
    _upgrade_predecessors(operations)
    revision = _load_revision(operations, REVISION_FILENAME)
    revision.upgrade()
    operations.events.clear()

    revision.downgrade()

    assert operations.events == [
        ("drop_index", "ix_memories_owner_category"),
        ("drop_index", "ix_memories_owner_updated_at"),
        ("drop_table", "memories"),
        ("drop_table", "memory_settings"),
    ]
    assert "documents" in operations.metadata.tables
    assert "users" in operations.metadata.tables
