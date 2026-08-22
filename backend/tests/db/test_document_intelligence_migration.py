import app.models  # noqa: F401  # Populate the model registry.
from app.db.base import Base
from tests.db.test_initial_domain_migration import (
    RecordingOperations,
    _load_revision,
    _revision_path,
    _table_signature,
)


REVISION_FILENAME = "0005_document_intelligence.py"


def _upgrade_predecessors(operations: RecordingOperations) -> None:
    for filename in (
        "0001_initial_domain.py",
        "0002_user_access_credential.py",
        "0003_bound_message_content.py",
        "0004_owned_assets.py",
    ):
        _load_revision(operations, filename).upgrade()


def test_document_revision_follows_owned_assets_revision():
    operations = RecordingOperations()
    revision = _load_revision(operations, REVISION_FILENAME)

    assert _revision_path(REVISION_FILENAME).name == REVISION_FILENAME
    assert revision.revision == "0005_document_intelligence"
    assert revision.down_revision == "0004_owned_assets"
    assert revision.branch_labels is None
    assert revision.depends_on is None


def test_upgrade_matches_exact_document_and_citation_orm_schema():
    operations = RecordingOperations()
    _upgrade_predecessors(operations)
    operations.events.clear()

    _load_revision(operations, REVISION_FILENAME).upgrade()

    assert operations.events == [
        ("create_table", "documents"),
        ("create_index", "ix_documents_owner_status"),
        ("create_index", "ix_documents_owner_updated_at"),
        ("create_table", "document_chunks"),
        ("create_index", "ix_document_chunks_owner_document_ordinal"),
        ("create_table", "message_citations"),
    ]
    for table_name in ("documents", "document_chunks", "message_citations"):
        assert _table_signature(operations.metadata.tables[table_name]) == (
            _table_signature(Base.metadata.tables[table_name])
        )


def test_downgrade_removes_document_relations_in_dependency_order():
    operations = RecordingOperations()
    _upgrade_predecessors(operations)
    revision = _load_revision(operations, REVISION_FILENAME)
    revision.upgrade()
    operations.events.clear()

    revision.downgrade()

    assert operations.events == [
        ("drop_table", "message_citations"),
        ("drop_index", "ix_document_chunks_owner_document_ordinal"),
        ("drop_table", "document_chunks"),
        ("drop_index", "ix_documents_owner_updated_at"),
        ("drop_index", "ix_documents_owner_status"),
        ("drop_table", "documents"),
    ]
    assert "assets" in operations.metadata.tables
    assert "messages" in operations.metadata.tables
