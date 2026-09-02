import asyncio

from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI

import app.core.lifespan as lifespan_module


@pytest.mark.asyncio
async def test_lifespan_rejects_provisioning_without_database_before_acquisition(
    monkeypatch,
):
    app = FastAPI()
    detect_hardware = Mock()
    create_postgres_engine = Mock()
    provisioning_digest = "a" * 64
    monkeypatch.setattr(
        lifespan_module.settings,
        "USER_PROVISIONING_TOKEN_DIGEST",
        provisioning_digest,
    )
    monkeypatch.setattr(lifespan_module.settings, "DATABASE_URL", None)
    monkeypatch.setattr(lifespan_module, "detect_hardware", detect_hardware)
    monkeypatch.setattr(
        lifespan_module,
        "create_postgres_engine",
        create_postgres_engine,
    )

    with pytest.raises(
        RuntimeError,
        match=(
            "DATABASE_URL must be configured when user provisioning is enabled"
        ),
    ) as caught:
        async with lifespan_module.lifespan(app):
            pytest.fail("lifespan must reject unusable provisioning configuration")

    assert provisioning_digest not in str(caught.value)
    detect_hardware.assert_not_called()
    create_postgres_engine.assert_not_called()


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
        assert app.state.model_catalog.max_list_discovery_seconds == 60.0
        assert await app.state.model_catalog.list_models() == ()
        assert app.state.task_model_router is not None
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
    reconcile_tools = AsyncMock(return_value=0)
    reconcile_workflow_rows = AsyncMock(return_value=0)
    workflow_runner = Mock(shutdown=AsyncMock())
    workflow_runner_factory = Mock(return_value=workflow_runner)
    marketing_runner = Mock(
        reconcile_interrupted=AsyncMock(return_value=0),
        shutdown=AsyncMock(),
    )
    marketing_runner_factory = Mock(return_value=marketing_runner)
    monkeypatch.setattr(
        lifespan_module, "reconcile_tool_executions", reconcile_tools
    )
    monkeypatch.setattr(
        lifespan_module, "reconcile_workflows", reconcile_workflow_rows
    )
    monkeypatch.setattr(
        lifespan_module, "WorkflowRunner", workflow_runner_factory
    )
    monkeypatch.setattr(
        lifespan_module, "MarketingCampaignRunner", marketing_runner_factory
    )

    async with lifespan_module.lifespan(app):
        assert app.state.postgres_engine is engine
        assert app.state.db_session_factory is factory
        assert (
            app.state.generation_admission_controller.max_active == 1
        )
        assert app.state.generation_max_duration_seconds == 180.0
        assert app.state.model_list_max_response_bytes == 1_048_576
        assert app.state.model_catalog.max_list_discovery_seconds == 60.0
        assert await app.state.model_catalog.list_models() == ()
        assert app.state.text_generation_router._runtimes == {}
        assert app.state.workflow_runner is workflow_runner
        assert app.state.marketing_campaign_runner is marketing_runner
        workflow_runner.shutdown.assert_not_awaited()
        marketing_runner.shutdown.assert_not_awaited()
        dispose_postgres.assert_not_awaited()

    create_session_factory.assert_called_once_with(engine)
    reconcile_tools.assert_awaited_once_with(factory)
    reconcile_workflow_rows.assert_awaited_once_with(factory)
    workflow_runner_factory.assert_called_once()
    marketing_runner_factory.assert_called_once()
    marketing_runner.reconcile_interrupted.assert_awaited_once_with()
    workflow_runner.shutdown.assert_awaited_once_with()
    marketing_runner.shutdown.assert_awaited_once_with()
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
        "OLLAMA_TASK_MODEL_PREFERENCES",
        {"code_generation": "verified-local:latest"},
    )
    monkeypatch.setattr(
        lifespan_module.settings,
        "OLLAMA_EMBEDDING_MODEL",
        "verified-local:latest",
    )
    monkeypatch.setattr(
        lifespan_module.settings,
        "MODEL_LIST_MAX_DISCOVERY_SECONDS",
        42.5,
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
        assert app.state.model_catalog.max_list_discovery_seconds == 42.5
        assert len(app.state.model_catalog.runtimes) == 1
        runtime = app.state.model_catalog.runtimes[0]
        assert runtime.runtime_id == "ollama-local"
        assert runtime.client is ollama_client
        assert runtime.max_response_bytes == 45_678
        assert runtime.max_list_models == 37
        assert not hasattr(runtime, "model_list_max_response_bytes")
        assert not hasattr(runtime, "max_list_discovery_seconds")
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
        assert app.state.task_model_router.preferred_model_ids == {
            lifespan_module.ModelTask.CODE_GENERATION: (
                lifespan_module.public_model_id(
                    "ollama-local",
                    "verified-local:latest",
                )
            )
        }
        embedding_runtime = app.state.document_embedding_runtime
        assert embedding_runtime.client is ollama_client
        assert embedding_runtime.model_reference == "verified-local:latest"
        assert embedding_runtime.model_id == "ollama:verified-local:latest"
        assert not hasattr(generation_runtime, "max_list_models")
        assert not hasattr(
            generation_runtime,
            "model_list_max_response_bytes",
        )
        assert not hasattr(
            generation_runtime,
            "max_list_discovery_seconds",
        )

    close_ollama.assert_awaited_once_with(ollama_client)

def _install_resource_lifecycle_mocks(monkeypatch):
    events: list[str] = []
    postgres_engine = object()
    redis_client = object()
    ollama_client = object()
    session_factory = object()

    def create_postgres(_settings):
        events.append("create_postgres")
        return postgres_engine

    def create_redis(_settings):
        events.append("create_redis")
        return redis_client

    def create_ollama(_settings):
        events.append("create_ollama")
        return ollama_client

    async def dispose_postgres(resource):
        assert resource is postgres_engine
        events.append("dispose_postgres")

    async def close_redis(resource):
        assert resource is redis_client
        events.append("close_redis")

    async def close_ollama(resource):
        assert resource is ollama_client
        events.append("close_ollama")

    workflow_runner = Mock(shutdown=AsyncMock())
    marketing_runner = Mock(
        reconcile_interrupted=AsyncMock(return_value=0),
        shutdown=AsyncMock(),
    )
    resources = {
        "events": events,
        "postgres_engine": postgres_engine,
        "redis_client": redis_client,
        "ollama_client": ollama_client,
        "session_factory": session_factory,
        "create_postgres": Mock(side_effect=create_postgres),
        "create_redis": Mock(side_effect=create_redis),
        "create_ollama": Mock(side_effect=create_ollama),
        "create_session_factory": Mock(return_value=session_factory),
        "reconcile_tools": AsyncMock(return_value=0),
        "reconcile_workflows": AsyncMock(return_value=0),
        "workflow_runner": workflow_runner,
        "workflow_runner_factory": Mock(return_value=workflow_runner),
        "marketing_runner": marketing_runner,
        "marketing_runner_factory": Mock(return_value=marketing_runner),
        "dispose_postgres": AsyncMock(side_effect=dispose_postgres),
        "close_redis": AsyncMock(side_effect=close_redis),
        "close_ollama": AsyncMock(side_effect=close_ollama),
    }
    monkeypatch.setattr(
        lifespan_module,
        "create_postgres_engine",
        resources["create_postgres"],
    )
    monkeypatch.setattr(
        lifespan_module,
        "create_redis_client",
        resources["create_redis"],
    )
    monkeypatch.setattr(
        lifespan_module,
        "create_ollama_client",
        resources["create_ollama"],
    )
    monkeypatch.setattr(
        lifespan_module,
        "create_session_factory",
        resources["create_session_factory"],
    )
    monkeypatch.setattr(
        lifespan_module,
        "reconcile_tool_executions",
        resources["reconcile_tools"],
    )
    monkeypatch.setattr(
        lifespan_module,
        "reconcile_workflows",
        resources["reconcile_workflows"],
    )
    monkeypatch.setattr(
        lifespan_module,
        "WorkflowRunner",
        resources["workflow_runner_factory"],
    )
    monkeypatch.setattr(
        lifespan_module,
        "MarketingCampaignRunner",
        resources["marketing_runner_factory"],
    )
    monkeypatch.setattr(
        lifespan_module,
        "dispose_postgres",
        resources["dispose_postgres"],
    )
    monkeypatch.setattr(
        lifespan_module,
        "close_redis",
        resources["close_redis"],
    )
    monkeypatch.setattr(
        lifespan_module,
        "close_ollama",
        resources["close_ollama"],
    )
    return resources


@pytest.mark.asyncio
async def test_lifespan_acquires_without_probes_and_closes_in_reverse_order(
    monkeypatch,
):
    app = FastAPI()
    resources = _install_resource_lifecycle_mocks(monkeypatch)
    reached_yield = False

    async with lifespan_module.lifespan(app):
        reached_yield = True
        assert resources["events"] == [
            "create_postgres",
            "create_redis",
            "create_ollama",
        ]
        assert app.state.postgres_engine is resources["postgres_engine"]
        assert app.state.db_session_factory is resources["session_factory"]
        assert app.state.redis_client is resources["redis_client"]
        assert app.state.ollama_client is resources["ollama_client"]
        resources["dispose_postgres"].assert_not_awaited()
        resources["close_redis"].assert_not_awaited()
        resources["close_ollama"].assert_not_awaited()

    assert reached_yield is True
    assert resources["events"] == [
        "create_postgres",
        "create_redis",
        "create_ollama",
        "close_ollama",
        "close_redis",
        "dispose_postgres",
    ]


@pytest.mark.asyncio
async def test_postgres_constructor_failure_closes_nothing(monkeypatch):
    app = FastAPI()
    resources = _install_resource_lifecycle_mocks(monkeypatch)
    failure = RuntimeError("postgres constructor failed")
    resources["create_postgres"].side_effect = failure
    reached_yield = False

    with pytest.raises(RuntimeError) as captured:
        async with lifespan_module.lifespan(app):
            reached_yield = True

    assert captured.value is failure
    assert reached_yield is False
    resources["create_redis"].assert_not_called()
    resources["create_ollama"].assert_not_called()
    resources["dispose_postgres"].assert_not_awaited()
    resources["close_redis"].assert_not_awaited()
    resources["close_ollama"].assert_not_awaited()


@pytest.mark.asyncio
async def test_redis_constructor_failure_disposes_postgres(monkeypatch):
    app = FastAPI()
    resources = _install_resource_lifecycle_mocks(monkeypatch)
    failure = RuntimeError("redis constructor failed")
    resources["create_redis"].side_effect = failure
    reached_yield = False

    with pytest.raises(RuntimeError) as captured:
        async with lifespan_module.lifespan(app):
            reached_yield = True

    assert captured.value is failure
    assert reached_yield is False
    resources["create_ollama"].assert_not_called()
    resources["dispose_postgres"].assert_awaited_once_with(
        resources["postgres_engine"]
    )
    resources["close_redis"].assert_not_awaited()
    resources["close_ollama"].assert_not_awaited()
    assert resources["events"] == ["create_postgres", "dispose_postgres"]


@pytest.mark.asyncio
async def test_ollama_constructor_failure_closes_redis_then_postgres(
    monkeypatch,
):
    app = FastAPI()
    resources = _install_resource_lifecycle_mocks(monkeypatch)
    failure = RuntimeError("ollama constructor failed")
    resources["create_ollama"].side_effect = failure
    reached_yield = False

    with pytest.raises(RuntimeError) as captured:
        async with lifespan_module.lifespan(app):
            reached_yield = True

    assert captured.value is failure
    assert reached_yield is False
    resources["close_ollama"].assert_not_awaited()
    assert resources["events"] == [
        "create_postgres",
        "create_redis",
        "close_redis",
        "dispose_postgres",
    ]


@pytest.mark.asyncio
async def test_app_state_publication_failure_closes_all_resources(monkeypatch):
    failure = RuntimeError("state publication failed")

    class FailingState:
        def __setattr__(self, name, value):
            if name == "db_session_factory":
                raise failure
            object.__setattr__(self, name, value)

    app = FastAPI()
    app.state = FailingState()
    resources = _install_resource_lifecycle_mocks(monkeypatch)
    reached_yield = False

    with pytest.raises(RuntimeError) as captured:
        async with lifespan_module.lifespan(app):
            reached_yield = True

    assert captured.value is failure
    assert reached_yield is False
    assert resources["events"] == [
        "create_postgres",
        "create_redis",
        "create_ollama",
        "close_ollama",
        "close_redis",
        "dispose_postgres",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "setup_symbol",
    [
        "GenerationAdmissionController",
        "ModelCatalog",
        "OllamaTextGenerationRuntime",
        "TaskAwareModelRouter",
        "TextGenerationRouter",
    ],
)
async def test_later_setup_failure_closes_all_resources(
    monkeypatch,
    setup_symbol,
):
    app = FastAPI()
    resources = _install_resource_lifecycle_mocks(monkeypatch)
    failure = RuntimeError(f"{setup_symbol} construction failed")
    monkeypatch.setattr(
        lifespan_module,
        setup_symbol,
        Mock(side_effect=failure),
    )
    reached_yield = False

    with pytest.raises(RuntimeError) as captured:
        async with lifespan_module.lifespan(app):
            reached_yield = True

    assert captured.value is failure
    assert reached_yield is False
    assert resources["events"] == [
        "create_postgres",
        "create_redis",
        "create_ollama",
        "close_ollama",
        "close_redis",
        "dispose_postgres",
    ]


@pytest.mark.asyncio
async def test_body_exception_closes_all_resources_and_propagates(monkeypatch):
    app = FastAPI()
    resources = _install_resource_lifecycle_mocks(monkeypatch)
    failure = RuntimeError("lifespan body failed")

    with pytest.raises(RuntimeError) as captured:
        async with lifespan_module.lifespan(app):
            raise failure

    assert captured.value is failure
    assert resources["events"] == [
        "create_postgres",
        "create_redis",
        "create_ollama",
        "close_ollama",
        "close_redis",
        "dispose_postgres",
    ]


@pytest.mark.asyncio
async def test_ollama_close_failure_does_not_skip_later_closers(monkeypatch):
    app = FastAPI()
    resources = _install_resource_lifecycle_mocks(monkeypatch)
    failure = RuntimeError("ollama close failed")

    async def fail_ollama_close(resource):
        assert resource is resources["ollama_client"]
        resources["events"].append("close_ollama")
        raise failure

    resources["close_ollama"].side_effect = fail_ollama_close

    with pytest.raises(RuntimeError) as captured:
        async with lifespan_module.lifespan(app):
            pass

    assert captured.value is failure
    assert resources["events"] == [
        "create_postgres",
        "create_redis",
        "create_ollama",
        "close_ollama",
        "close_redis",
        "dispose_postgres",
    ]


@pytest.mark.asyncio
async def test_redis_close_failure_does_not_skip_postgres_disposal(monkeypatch):
    app = FastAPI()
    resources = _install_resource_lifecycle_mocks(monkeypatch)
    failure = RuntimeError("redis close failed")

    async def fail_redis_close(resource):
        assert resource is resources["redis_client"]
        resources["events"].append("close_redis")
        raise failure

    resources["close_redis"].side_effect = fail_redis_close

    with pytest.raises(RuntimeError) as captured:
        async with lifespan_module.lifespan(app):
            pass

    assert captured.value is failure
    assert resources["events"] == [
        "create_postgres",
        "create_redis",
        "create_ollama",
        "close_ollama",
        "close_redis",
        "dispose_postgres",
    ]


@pytest.mark.asyncio
async def test_multiple_closer_failures_attempt_all_and_propagate_last(
    monkeypatch,
):
    app = FastAPI()
    resources = _install_resource_lifecycle_mocks(monkeypatch)
    ollama_failure = RuntimeError("ollama close failed")
    redis_failure = RuntimeError("redis close failed")
    postgres_failure = RuntimeError("postgres dispose failed")

    async def fail_ollama_close(_resource):
        resources["events"].append("close_ollama")
        raise ollama_failure

    async def fail_redis_close(_resource):
        resources["events"].append("close_redis")
        raise redis_failure

    async def fail_postgres_dispose(_resource):
        resources["events"].append("dispose_postgres")
        raise postgres_failure

    resources["close_ollama"].side_effect = fail_ollama_close
    resources["close_redis"].side_effect = fail_redis_close
    resources["dispose_postgres"].side_effect = fail_postgres_dispose

    with pytest.raises(RuntimeError) as captured:
        async with lifespan_module.lifespan(app):
            pass

    assert captured.value is postgres_failure
    assert resources["events"] == [
        "create_postgres",
        "create_redis",
        "create_ollama",
        "close_ollama",
        "close_redis",
        "dispose_postgres",
    ]


@pytest.mark.asyncio
async def test_startup_cancellation_cleans_acquired_resources(monkeypatch):
    app = FastAPI()
    resources = _install_resource_lifecycle_mocks(monkeypatch)
    resources["create_ollama"].side_effect = asyncio.CancelledError()
    reached_yield = False

    with pytest.raises(asyncio.CancelledError):
        async with lifespan_module.lifespan(app):
            reached_yield = True

    assert reached_yield is False
    resources["close_ollama"].assert_not_awaited()
    assert resources["events"] == [
        "create_postgres",
        "create_redis",
        "close_redis",
        "dispose_postgres",
    ]


@pytest.mark.asyncio
async def test_body_cancellation_closes_all_resources_and_propagates(
    monkeypatch,
):
    app = FastAPI()
    resources = _install_resource_lifecycle_mocks(monkeypatch)

    with pytest.raises(asyncio.CancelledError):
        async with lifespan_module.lifespan(app):
            raise asyncio.CancelledError

    assert resources["events"] == [
        "create_postgres",
        "create_redis",
        "create_ollama",
        "close_ollama",
        "close_redis",
        "dispose_postgres",
    ]


@pytest.mark.asyncio
async def test_shutdown_closer_cancellation_attempts_remaining_cleanup(
    monkeypatch,
):
    app = FastAPI()
    resources = _install_resource_lifecycle_mocks(monkeypatch)

    async def cancel_ollama_close(resource):
        assert resource is resources["ollama_client"]
        resources["events"].append("close_ollama")
        raise asyncio.CancelledError

    resources["close_ollama"].side_effect = cancel_ollama_close

    with pytest.raises(asyncio.CancelledError):
        async with lifespan_module.lifespan(app):
            pass

    assert resources["events"] == [
        "create_postgres",
        "create_redis",
        "create_ollama",
        "close_ollama",
        "close_redis",
        "dispose_postgres",
    ]
