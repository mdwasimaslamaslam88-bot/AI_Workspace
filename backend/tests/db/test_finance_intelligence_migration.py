import app.models  # noqa: F401
from app.db.base import Base
from tests.db.test_initial_domain_migration import (
    RecordingOperations,
    _load_revision,
    _revision_path,
    _table_signature,
)
from tests.db.test_marketing_campaigns_migration import _upgrade_through_connectors


REVISION_FILENAME = "0015_finance_intelligence.py"


def _upgrade_through_marketing(operations: RecordingOperations) -> None:
    _upgrade_through_connectors(operations)
    _load_revision(operations, "0014_marketing_campaigns.py").upgrade()


def test_finance_revision_follows_marketing():
    operations = RecordingOperations()
    revision = _load_revision(operations, REVISION_FILENAME)

    assert _revision_path(REVISION_FILENAME).name == REVISION_FILENAME
    assert revision.revision == "0015_finance_intelligence"
    assert revision.down_revision == "0014_marketing_campaigns"
    assert revision.branch_labels is None
    assert revision.depends_on is None


def test_upgrade_matches_exact_finance_orm_schema():
    operations = RecordingOperations()
    _upgrade_through_marketing(operations)
    operations.events.clear()

    _load_revision(operations, REVISION_FILENAME).upgrade()

    assert operations.events == [
        ("create_table", "finance_workspaces"),
        ("create_table", "market_watch_items"),
        ("create_table", "paper_positions"),
        ("create_table", "paper_orders"),
        ("create_table", "market_alerts"),
        ("create_table", "finance_artifacts"),
        ("create_index", "ix_finance_workspaces_owner_created_at"),
        ("create_index", "ix_paper_orders_workspace_created_at"),
        ("create_index", "ix_market_alerts_workspace_status"),
        ("create_index", "ix_finance_artifacts_workspace_created_at"),
    ]
    for table_name in (
        "finance_workspaces",
        "market_watch_items",
        "paper_positions",
        "paper_orders",
        "market_alerts",
        "finance_artifacts",
    ):
        assert _table_signature(operations.metadata.tables[table_name]) == (
            _table_signature(Base.metadata.tables[table_name])
        )


def test_downgrade_removes_finance_relations_in_dependency_order():
    operations = RecordingOperations()
    _upgrade_through_marketing(operations)
    revision = _load_revision(operations, REVISION_FILENAME)
    revision.upgrade()
    operations.events.clear()

    revision.downgrade()

    assert operations.events == [
        ("drop_index", "ix_finance_artifacts_workspace_created_at"),
        ("drop_index", "ix_market_alerts_workspace_status"),
        ("drop_index", "ix_paper_orders_workspace_created_at"),
        ("drop_index", "ix_finance_workspaces_owner_created_at"),
        ("drop_table", "finance_artifacts"),
        ("drop_table", "market_alerts"),
        ("drop_table", "paper_orders"),
        ("drop_table", "paper_positions"),
        ("drop_table", "market_watch_items"),
        ("drop_table", "finance_workspaces"),
    ]
