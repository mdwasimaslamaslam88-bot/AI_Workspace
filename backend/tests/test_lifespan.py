from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI

import app.core.lifespan as lifespan_module


@pytest.mark.asyncio
async def test_lifespan_exposes_unconfigured_database_factory(monkeypatch):
    app = FastAPI()
    create_session_factory = Mock(return_value=None)
    dispose_postgres = AsyncMock()
    close_redis = AsyncMock()
    close_ollama = AsyncMock()
    monkeypatch.setattr(lifespan_module, "create_postgres_engine", Mock(return_value=None))
    monkeypatch.setattr(lifespan_module, "create_redis_client", Mock(return_value=None))
    monkeypatch.setattr(lifespan_module, "create_ollama_client", Mock(return_value=None))
    monkeypatch.setattr(lifespan_module, "create_session_factory", create_session_factory)
    monkeypatch.setattr(lifespan_module, "dispose_postgres", dispose_postgres)
    monkeypatch.setattr(lifespan_module, "close_redis", close_redis)
    monkeypatch.setattr(lifespan_module, "close_ollama", close_ollama)

    async with lifespan_module.lifespan(app):
        assert app.state.postgres_engine is None
        assert app.state.db_session_factory is None
        assert app.state.redis_client is None
        assert app.state.ollama_client is None
        assert (
            app.state.generation_admission_controller.max_active == 1
        )
        assert app.state.generation_max_duration_seconds == 180.0
        assert app.state.model_list_max_response_bytes == 1_048_576
        assert await app.state.model_catalog.list_models() == ()
        assert app.state.text_generation_router._runtimes == {}

    create_session_factory.assert_called_once_with(None)
    dispose_postgres.assert_awaited_once_with(None)
    close_redis.assert_awaited_once_with(None)
    close_ollama.assert_awaited_once_with(None)


@pytest.mark.asyncio
async def test_lifespan_creates_factory_and_disposes_configured_engine(monkeypatch):
    app = FastAPI()
    engine = object()
    factory = object()
    create_session_factory = Mock(return_value=factory)
    dispose_postgres = AsyncMock()
    close_redis = AsyncMock()
    close_ollama = AsyncMock()
    monkeypatch.setattr(lifespan_module, "create_postgres_engine", Mock(return_value=engine))
    monkeypatch.setattr(lifespan_module, "create_redis_client", Mock(return_value=None))
    monkeypatch.setattr(lifespan_module, "create_ollama_client", Mock(return_value=None))
    monkeypatch.setattr(lifespan_module, "create_session_factory", create_session_factory)
    monkeypatch.setattr(lifespan_module, "dispose_postgres", dispose_postgres)
    monkeypatch.setattr(lifespan_module, "close_redis", close_redis)
    monkeypatch.setattr(lifespan_module, "close_ollama", close_ollama)

    async with lifespan_module.lifespan(app):
        assert app.state.postgres_engine is engine
        assert app.state.db_session_factory is factory
        assert (
            app.state.generation_admission_controller.max_active == 1
        )
        assert app.state.generation_max_duration_seconds == 180.0
        assert app.state.model_list_max_response_bytes == 1_048_576
        assert await app.state.model_catalog.list_models() == ()
        assert app.state.text_generation_router._runtimes == {}
        dispose_postgres.assert_not_awaited()

    create_session_factory.assert_called_once_with(engine)
    dispose_postgres.assert_awaited_once_with(engine)
    close_redis.assert_awaited_once_with(None)
    close_ollama.assert_awaited_once_with(None)


@pytest.mark.asyncio
async def test_lifespan_registers_configured_local_runtime_catalog(monkeypatch):
    app = FastAPI()
    ollama_client = object()
    create_session_factory = Mock(return_value=None)
    dispose_postgres = AsyncMock()
    close_redis = AsyncMock()
    close_ollama = AsyncMock()
    monkeypatch.setattr(
        lifespan_module,
        "create_postgres_engine",
        Mock(return_value=None),
    )
    monkeypatch.setattr(
        lifespan_module,
        "create_redis_client",
        Mock(return_value=None),
    )
    monkeypatch.setattr(
        lifespan_module,
        "create_ollama_client",
        Mock(return_value=ollama_client),
    )
    monkeypatch.setattr(
        lifespan_module,
        "create_session_factory",
        create_session_factory,
    )
    monkeypatch.setattr(lifespan_module, "dispose_postgres", dispose_postgres)
    monkeypatch.setattr(lifespan_module, "close_redis", close_redis)
    monkeypatch.setattr(lifespan_module, "close_ollama", close_ollama)
    monkeypatch.setattr(
        lifespan_module.settings,
        "OLLAMA_LOCAL_MODEL_ALLOWLIST",
        ("verified-local:latest",),
    )
    monkeypatch.setattr(
        lifespan_module.settings,
        "MODEL_LIST_MAX_RESPONSE_BYTES",
        98_765,
    )
    monkeypatch.setattr(
        lifespan_module.settings,
        "OLLAMA_CATALOG_MAX_RESPONSE_BYTES",
        45_678,
    )
    monkeypatch.setattr(
        lifespan_module.settings,
        "OLLAMA_CATALOG_MAX_LIST_MODELS",
        37,
    )
    monkeypatch.setattr(
        lifespan_module.settings,
        "OLLAMA_GENERATION_MAX_REQUEST_BYTES",
        54_321,
    )
    monkeypatch.setattr(
        lifespan_module.settings,
        "OLLAMA_GENERATION_MAX_RESPONSE_BYTES",
        12_345,
    )
    monkeypatch.setattr(
        lifespan_module.settings,
        "GENERATION_MAX_DURATION_SECONDS",
        73.25,
    )

    async with lifespan_module.lifespan(app):
        assert (
            app.state.generation_admission_controller.max_active == 1
        )
        assert app.state.generation_max_duration_seconds == 73.25
        assert app.state.model_list_max_response_bytes == 98_765
        assert len(app.state.model_catalog.runtimes) == 1
        runtime = app.state.model_catalog.runtimes[0]
        assert runtime.runtime_id == "ollama-local"
        assert runtime.client is ollama_client
        assert runtime.max_response_bytes == 45_678
        assert runtime.max_list_models == 37
        assert not hasattr(runtime, "model_list_max_response_bytes")
        assert runtime.local_model_allowlist == {"verified-local:latest"}
        generation_runtime = (
            app.state.text_generation_router._runtimes["ollama-local"]
        )
        assert generation_runtime.client is ollama_client
        assert generation_runtime.timeout_seconds == 120.0
        assert generation_runtime.max_request_bytes == 54_321
        assert generation_runtime.max_response_bytes == 12_345
        assert generation_runtime.local_model_allowlist == {
            "verified-local:latest"
        }
        assert not hasattr(generation_runtime, "max_list_models")
        assert not hasattr(
            generation_runtime,
            "model_list_max_response_bytes",
        )

    close_ollama.assert_awaited_once_with(ollama_client)
