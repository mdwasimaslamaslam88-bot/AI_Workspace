from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI

import app.core.lifespan as lifespan_module


@pytest.mark.asyncio
async def test_lifespan_leaves_asset_storage_unconfigured_without_affecting_startup(
    monkeypatch,
):
    app = FastAPI()
    monkeypatch.setattr(lifespan_module.settings, "ASSET_STORAGE_ROOT", None)
    monkeypatch.setattr(lifespan_module, "create_postgres_engine", Mock(return_value=None))
    monkeypatch.setattr(lifespan_module, "create_session_factory", Mock(return_value=None))
    monkeypatch.setattr(lifespan_module, "create_redis_client", Mock(return_value=None))
    monkeypatch.setattr(lifespan_module, "create_ollama_client", Mock(return_value=None))
    monkeypatch.setattr(lifespan_module, "dispose_postgres", AsyncMock())
    monkeypatch.setattr(lifespan_module, "close_redis", AsyncMock())
    monkeypatch.setattr(lifespan_module, "close_ollama", AsyncMock())
    storage_factory = Mock()
    reconcile = AsyncMock()
    monkeypatch.setattr(lifespan_module, "LocalAssetStorage", storage_factory)
    monkeypatch.setattr(lifespan_module, "reconcile_asset_storage", reconcile)

    async with lifespan_module.lifespan(app):
        assert app.state.asset_storage is None

    storage_factory.assert_not_called()
    reconcile.assert_not_awaited()


@pytest.mark.asyncio
async def test_lifespan_initializes_and_reconciles_configured_storage(
    tmp_path,
    monkeypatch,
):
    app = FastAPI()
    root = tmp_path / "assets"
    storage = object()
    session_factory = object()
    monkeypatch.setattr(lifespan_module.settings, "ASSET_STORAGE_ROOT", root)
    monkeypatch.setattr(lifespan_module, "create_postgres_engine", Mock(return_value=None))
    monkeypatch.setattr(
        lifespan_module,
        "create_session_factory",
        Mock(return_value=session_factory),
    )
    monkeypatch.setattr(lifespan_module, "create_redis_client", Mock(return_value=None))
    monkeypatch.setattr(lifespan_module, "create_ollama_client", Mock(return_value=None))
    monkeypatch.setattr(lifespan_module, "dispose_postgres", AsyncMock())
    monkeypatch.setattr(lifespan_module, "close_redis", AsyncMock())
    monkeypatch.setattr(lifespan_module, "close_ollama", AsyncMock())
    reconcile_tools = AsyncMock(return_value=0)
    reconcile_workflow_rows = AsyncMock(return_value=0)
    workflow_runner = Mock(shutdown=AsyncMock())
    marketing_runner = Mock(
        reconcile_interrupted=AsyncMock(return_value=0),
        shutdown=AsyncMock(),
    )
    monkeypatch.setattr(
        lifespan_module, "reconcile_tool_executions", reconcile_tools
    )
    monkeypatch.setattr(
        lifespan_module, "reconcile_workflows", reconcile_workflow_rows
    )
    monkeypatch.setattr(
        lifespan_module,
        "WorkflowRunner",
        Mock(return_value=workflow_runner),
    )
    monkeypatch.setattr(
        lifespan_module,
        "MarketingCampaignRunner",
        Mock(return_value=marketing_runner),
    )
    storage_factory = Mock(return_value=storage)
    reconcile = AsyncMock()
    monkeypatch.setattr(lifespan_module, "LocalAssetStorage", storage_factory)
    monkeypatch.setattr(lifespan_module, "reconcile_asset_storage", reconcile)

    async with lifespan_module.lifespan(app):
        assert app.state.asset_storage is storage
        assert app.state.marketing_campaign_runner is marketing_runner
        reconcile_tools.assert_awaited_once_with(session_factory)
        reconcile_workflow_rows.assert_awaited_once_with(session_factory)
        reconcile.assert_awaited_once_with(session_factory, storage)
        workflow_runner.shutdown.assert_not_awaited()

    workflow_runner.shutdown.assert_awaited_once_with()
    storage_factory.assert_called_once_with(root)
