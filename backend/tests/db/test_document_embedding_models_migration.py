import app.models  # noqa: F401
from app.db.base import Base
from tests.db.test_initial_domain_migration import (
    RecordingOperations,
    _load_revision,
    _revision_path,
    _table_signature,
)


REVISION_FILENAME = "0009_model_agnostic_document_embeddings.py"


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
    ):
        _load_revision(operations, filename).upgrade()


def test_revision_follows_bounded_workflows():
    operations = RecordingOperations()
    revision = _load_revision(operations, REVISION_FILENAME)

    assert _revision_path(REVISION_FILENAME).name == REVISION_FILENAME
    assert revision.revision == "0009_document_embedding_models"
    assert revision.down_revision == "0008_bounded_workflows"
    assert revision.branch_labels is None
    assert revision.depends_on is None


def test_upgrade_matches_model_agnostic_document_chunk_schema():
    operations = RecordingOperations()
    _upgrade_predecessors(operations)
    operations.events.clear()

    _load_revision(operations, REVISION_FILENAME).upgrade()

    assert operations.events == [
        ("add_column", "document_chunks.embedding_model"),
        ("add_column", "document_chunks.embedding_dimensions"),
        ("drop_constraint", "ck_document_chunks_embedding_dimensions_fixed"),
        (
            "create_check_constraint",
            "ck_document_chunks_embedding_dimensions_bounded",
        ),
        (
            "create_check_constraint",
            "ck_document_chunks_embedding_bytes_consistent",
        ),
        ("create_check_constraint", "ck_document_chunks_embedding_model_safe"),
        ("alter_column", "document_chunks.embedding_model"),
        ("alter_column", "document_chunks.embedding_dimensions"),
    ]
    assert _table_signature(operations.metadata.tables["document_chunks"]) == (
        _table_signature(Base.metadata.tables["document_chunks"])
    )


def test_downgrade_restores_legacy_embedding_schema_without_data_loss():
    operations = RecordingOperations()
    _upgrade_predecessors(operations)
    legacy_signature = _table_signature(
        operations.metadata.tables["document_chunks"]
    )
    revision = _load_revision(operations, REVISION_FILENAME)
    revision.upgrade()
    operations.events.clear()

    revision.downgrade()

    assert operations.events == [
        (
            "execute",
            "SELECT count(*) FROM document_chunks WHERE embedding_dimensions "
            "!= 256 OR octet_length(embedding) != 1024",
        ),
        ("drop_constraint", "ck_document_chunks_embedding_model_safe"),
        ("drop_constraint", "ck_document_chunks_embedding_bytes_consistent"),
        ("drop_constraint", "ck_document_chunks_embedding_dimensions_bounded"),
        ("create_check_constraint", "ck_document_chunks_embedding_dimensions_fixed"),
        ("drop_column", "document_chunks.embedding_dimensions"),
        ("drop_column", "document_chunks.embedding_model"),
    ]
    assert _table_signature(operations.metadata.tables["document_chunks"]) == (
        legacy_signature
    )
