import pytest

import app.models  # noqa: F401
from app.db.base import Base
from tests.db.test_initial_domain_migration import (
    RecordingOperations,
    _load_revision,
    _revision_path,
    _table_signature,
)


REVISION_FILENAME = "0010_asset_provenance.py"


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
    ):
        _load_revision(operations, filename).upgrade()


def test_asset_provenance_revision_extends_single_chain():
    revision = _load_revision(RecordingOperations(), REVISION_FILENAME)

    assert _revision_path(REVISION_FILENAME).name == REVISION_FILENAME
    assert revision.revision == "0010_asset_provenance"
    assert revision.down_revision == "0009_document_embedding_models"
    assert revision.branch_labels is None
    assert revision.depends_on is None


def test_asset_provenance_upgrade_matches_current_asset_schema():
    operations = RecordingOperations()
    _upgrade_predecessors(operations)
    operations.events.clear()

    _load_revision(operations, REVISION_FILENAME).upgrade()

    assert operations.events == [
        ("add_column", "assets.provenance_kind"),
        ("add_column", "assets.source_asset_id"),
        ("add_column", "assets.runtime_id"),
        ("add_column", "assets.model_id"),
        ("create_unique_constraint", "uq_assets_id_owner_id"),
        ("create_foreign_key", "fk_assets_source_asset_id_assets"),
        ("create_check_constraint", "ck_assets_provenance_kind_known"),
        ("create_check_constraint", "ck_assets_runtime_id_safe"),
        ("create_check_constraint", "ck_assets_model_id_public"),
        ("create_check_constraint", "ck_assets_provenance_consistent"),
        ("create_check_constraint", "ck_assets_source_not_self"),
        ("create_index", "ix_assets_source_asset_id"),
        ("alter_column", "assets.provenance_kind"),
    ]
    assert _table_signature(operations.metadata.tables["assets"]) == (
        _table_signature(Base.metadata.tables["assets"])
    )


def test_asset_provenance_downgrade_drops_safely_when_only_uploads_exist():
    operations = RecordingOperations()
    _upgrade_predecessors(operations)
    revision = _load_revision(operations, REVISION_FILENAME)
    revision.upgrade()
    operations.events.clear()

    revision.downgrade()

    assert operations.events == [
        ("execute", "SELECT count(*) FROM assets WHERE provenance_kind <> 'upload'"),
        ("drop_index", "ix_assets_source_asset_id"),
        ("drop_constraint", "ck_assets_source_not_self"),
        ("drop_constraint", "ck_assets_provenance_consistent"),
        ("drop_constraint", "ck_assets_model_id_public"),
        ("drop_constraint", "ck_assets_runtime_id_safe"),
        ("drop_constraint", "ck_assets_provenance_kind_known"),
        ("drop_constraint", "fk_assets_source_asset_id_assets"),
        ("drop_constraint", "uq_assets_id_owner_id"),
        ("drop_column", "assets.model_id"),
        ("drop_column", "assets.runtime_id"),
        ("drop_column", "assets.source_asset_id"),
        ("drop_column", "assets.provenance_kind"),
    ]


def test_asset_provenance_downgrade_refuses_generated_data(monkeypatch):
    operations = RecordingOperations()
    _upgrade_predecessors(operations)
    revision = _load_revision(operations, REVISION_FILENAME)
    revision.upgrade()
    operations.events.clear()

    class Result:
        @staticmethod
        def scalar_one():
            return 1

    original_execute = operations.execute

    def execute(statement, **kwargs):
        original_execute(statement, **kwargs)
        return Result()

    monkeypatch.setattr(operations, "execute", execute)

    with pytest.raises(
        RuntimeError,
        match="cannot downgrade while generated media assets exist",
    ):
        revision.downgrade()

    assert operations.events == [
        ("execute", "SELECT count(*) FROM assets WHERE provenance_kind <> 'upload'")
    ]
