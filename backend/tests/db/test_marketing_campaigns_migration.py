import app.models  # noqa: F401
from app.db.base import Base
from tests.db.test_initial_domain_migration import (
    RecordingOperations,
    _load_revision,
    _revision_path,
    _table_signature,
)
from tests.db.test_owner_connectors_migration import _upgrade_predecessors


REVISION_FILENAME = "0014_marketing_campaigns.py"


def _upgrade_through_connectors(operations: RecordingOperations) -> None:
    _upgrade_predecessors(operations)
    _load_revision(operations, "0013_owner_connectors.py").upgrade()


def test_marketing_revision_follows_owner_connectors():
    operations = RecordingOperations()
    revision = _load_revision(operations, REVISION_FILENAME)

    assert _revision_path(REVISION_FILENAME).name == REVISION_FILENAME
    assert revision.revision == "0014_marketing_campaigns"
    assert revision.down_revision == "0013_owner_connectors"
    assert revision.branch_labels is None
    assert revision.depends_on is None


def test_upgrade_matches_exact_marketing_orm_schema():
    operations = RecordingOperations()
    _upgrade_through_connectors(operations)
    operations.events.clear()

    _load_revision(operations, REVISION_FILENAME).upgrade()

    assert operations.events == [
        ("create_table", "marketing_campaigns"),
        ("create_table", "marketing_stages"),
        ("create_index", "ix_marketing_campaigns_owner_created_at"),
        ("create_index", "ix_marketing_campaigns_owner_status"),
        ("create_index", "ix_marketing_stages_owner_campaign_position"),
    ]
    for table_name in ("marketing_campaigns", "marketing_stages"):
        assert _table_signature(operations.metadata.tables[table_name]) == (
            _table_signature(Base.metadata.tables[table_name])
        )


def test_downgrade_removes_marketing_relations_in_dependency_order():
    operations = RecordingOperations()
    _upgrade_through_connectors(operations)
    revision = _load_revision(operations, REVISION_FILENAME)
    revision.upgrade()
    operations.events.clear()

    revision.downgrade()

    assert operations.events == [
        ("drop_index", "ix_marketing_stages_owner_campaign_position"),
        ("drop_index", "ix_marketing_campaigns_owner_status"),
        ("drop_index", "ix_marketing_campaigns_owner_created_at"),
        ("drop_table", "marketing_stages"),
        ("drop_table", "marketing_campaigns"),
    ]
