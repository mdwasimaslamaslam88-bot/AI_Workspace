import asyncio
from unittest.mock import AsyncMock, Mock, call

import httpx
import pytest
from fastapi import FastAPI

import app.ai.catalog as catalog_module
import app.api.v1.ai as ai_module
from app.ai.catalog import (
    ModelAvailability,
    ModelCapability,
    ModelCatalog,
    ModelDescriptor,
    ModelModality,
    ModelRuntimeUnavailableError,
    RuntimeModel,
)
from app.api.dependencies import get_current_user
from app.api.v1.ai import router as ai_router
from app.exceptions.handlers import register_exception_handlers


def _runtime(runtime_id: str, models: tuple[RuntimeModel, ...]) -> Mock:
    async def discover_models(*, reference_selector=None):
        if reference_selector is None:
            return models
        return tuple(
            model
            for model in models
            if reference_selector(model.reference)
        )

    runtime = Mock(runtime_id=runtime_id)
    runtime.supports_reference_selector = True
    runtime.discover_models = AsyncMock(side_effect=discover_models)
    return runtime


@pytest.mark.parametrize(
    "kwargs",
    [
        {"reference": "", "display_name": "Model"},
        {"reference": "tag", "display_name": "  "},
        {"reference": "tag", "display_name": "Model", "context_window": 0},
        {
            "reference": "tag",
            "display_name": "Model",
            "estimated_vram_bytes": True,
        },
        {"reference": "tag", "display_name": "Model", "family": ""},
        {
            "reference": "internal-tag",
            "display_name": "/private/runtime/model:7b",
        },
        {"reference": "tag", "display_name": "Model", "family": "https://remote"},
    ],
)
def test_runtime_model_descriptor_validation_rejects_invalid_values(kwargs):
    with pytest.raises((TypeError, ValueError)):
        RuntimeModel(**kwargs)


def test_public_descriptor_requires_matching_runtime_namespace():
    with pytest.raises(ValueError, match="namespace"):
        ModelDescriptor(
            model_id=f"other:{'a' * 24}",
            display_name="Safe model",
            runtime_id="local-runtime",
            modality=ModelModality.TEXT,
            family=None,
            parameter_class=None,
            capabilities=(),
            context_window=None,
            quantization=None,
            estimated_vram_bytes=None,
            availability=ModelAvailability.UNKNOWN,
        )


@pytest.mark.asyncio
async def test_catalog_returns_stable_opaque_runtime_namespaced_ids_and_order():
    raw_reference = "/private/models/secret-model:7b"
    runtime = _runtime(
        "local-runtime",
        (
            RuntimeModel(reference="z-tag", display_name="zeta"),
            RuntimeModel(reference=raw_reference, display_name="Alpha"),
        ),
    )
    catalog = ModelCatalog((runtime,))

    first = await catalog.list_models()
    second = await catalog.list_models()

    assert [model.display_name for model in first] == ["Alpha", "zeta"]
    assert [model.model_id for model in first] == [
        model.model_id for model in second
    ]
    assert all(model.model_id.startswith("local-runtime:") for model in first)
    assert all(len(model.model_id.split(":", 1)[1]) == 24 for model in first)
    assert raw_reference not in repr(first)
    assert runtime.discover_models.await_args_list == [call(), call()]


@pytest.mark.asyncio
async def test_catalog_normalizes_capabilities_and_preserves_unknown_metadata():
    runtime = _runtime(
        "local-runtime",
        (
            RuntimeModel(
                reference="model-tag",
                display_name="Model",
                capabilities=(
                    "CHAT",
                    "text-generation",
                    "chat",
                    "future-unknown-capability",
                ),
            ),
        ),
    )

    (model,) = await ModelCatalog((runtime,)).list_models()

    assert model.capabilities == (
        ModelCapability.CHAT,
        ModelCapability.TEXT_GENERATION,
    )
    assert model.family is None
    assert model.parameter_class is None
    assert model.context_window is None
    assert model.quantization is None
    assert model.estimated_vram_bytes is None


@pytest.mark.asyncio
async def test_unconfigured_catalog_is_an_empty_safe_inventory():
    assert await ModelCatalog().list_models() == ()


def test_catalog_rejects_duplicate_runtime_ids():
    first = _runtime("duplicate", ())
    second = _runtime("duplicate", ())

    with pytest.raises(ValueError, match="duplicate runtime_id"):
        ModelCatalog((first, second))


@pytest.mark.parametrize(
    "value",
    [True, False, "60", 0, -1, float("nan"), float("inf"), 300.0001],
)
def test_catalog_rejects_invalid_list_discovery_deadline(value):
    with pytest.raises((TypeError, ValueError)):
        ModelCatalog(max_list_discovery_seconds=value)


@pytest.mark.asyncio
async def test_catalog_rejects_duplicate_public_model_ids():
    duplicate = RuntimeModel(reference="same-tag", display_name="Same")
    catalog = ModelCatalog((_runtime("local-runtime", (duplicate, duplicate)),))

    with pytest.raises(ValueError, match="duplicate public model_id"):
        await catalog.list_models()


@pytest.mark.asyncio
async def test_catalog_resolves_public_id_to_internal_runtime_binding():
    raw_reference = "/private/runtime/model:32b"
    runtime = _runtime(
        "local-runtime",
        (
            RuntimeModel(
                reference=raw_reference,
                display_name="Local 32B",
                family="LocalFamily",
                parameter_class="32B",
                quantization="Q4_K_M",
                capabilities=(
                    ModelCapability.TEXT_GENERATION,
                    ModelCapability.TOOL_CALLING,
                ),
            ),
        ),
    )
    catalog = ModelCatalog((runtime,))
    (descriptor,) = await catalog.list_models()

    resolved = await catalog.resolve_model(descriptor.model_id)

    assert resolved is not None
    assert resolved.descriptor == descriptor
    assert resolved.runtime_reference == raw_reference
    assert raw_reference not in repr(descriptor)
    assert descriptor.model_id == "local-runtime:9d8957bf07732840f5b2c7ef"
    assert resolved.descriptor.capabilities == (
        ModelCapability.TEXT_GENERATION,
        ModelCapability.TOOL_CALLING,
    )
    selector = runtime.discover_models.await_args_list[1].kwargs[
        "reference_selector"
    ]
    assert selector(raw_reference) is True
    assert selector(f"{raw_reference}-different") is False
    assert runtime.discover_models.await_args_list == [
        call(),
        call(reference_selector=selector),
    ]


@pytest.mark.asyncio
async def test_catalog_same_namespace_missing_model_uses_targeted_discovery():
    reference = "/private/runtime/installed:7b"
    runtime = _runtime(
        "local-runtime",
        (RuntimeModel(reference=reference, display_name="Installed 7B"),),
    )

    resolved = await ModelCatalog((runtime,)).resolve_model(
        f"local-runtime:{'f' * 24}"
    )

    assert resolved is None
    runtime.discover_models.assert_awaited_once()
    selector = runtime.discover_models.await_args.kwargs["reference_selector"]
    assert selector(reference) is False


@pytest.mark.asyncio
async def test_catalog_targeted_resolution_retains_duplicate_public_id_defense():
    duplicate = RuntimeModel(reference="same-tag", display_name="Same")
    source_runtime = _runtime("local-runtime", (duplicate,))
    (descriptor,) = await ModelCatalog((source_runtime,)).list_models()
    duplicate_runtime = _runtime(
        "local-runtime",
        (duplicate, duplicate),
    )

    with pytest.raises(ValueError, match="duplicate public model_id"):
        await ModelCatalog((duplicate_runtime,)).resolve_model(
            descriptor.model_id
        )

    duplicate_runtime.discover_models.assert_awaited_once()
    assert "reference_selector" in (
        duplicate_runtime.discover_models.await_args.kwargs
    )


@pytest.mark.asyncio
async def test_catalog_unknown_runtime_namespace_does_not_invoke_discovery():
    runtime = _runtime("local-runtime", ())
    catalog = ModelCatalog((runtime,))

    resolved = await catalog.resolve_model(
        f"other-runtime:{'a' * 24}"
    )

    assert resolved is None
    runtime.discover_models.assert_not_awaited()
    assert catalog._list_models_flight is None


@pytest.mark.parametrize(
    "model_id",
    ["", "raw-tag", "LOCAL:" + "a" * 24, "local:short"],
)
@pytest.mark.asyncio
async def test_catalog_rejects_invalid_public_id_before_discovery(model_id):
    runtime = _runtime("local-runtime", ())
    catalog = ModelCatalog((runtime,))

    with pytest.raises(ValueError, match="runtime-namespaced"):
        await catalog.resolve_model(model_id)

    runtime.discover_models.assert_not_awaited()
    assert catalog._list_models_flight is None

def _runtime_with_discovery(runtime_id, discover_models):
    runtime = Mock(runtime_id=runtime_id)
    runtime.supports_reference_selector = True
    runtime.discover_models = AsyncMock(side_effect=discover_models)
    return runtime


def _signal_when_list_waiters_join(catalog, expected_count):
    joined = asyncio.Event()
    original_join = catalog._join_list_models_flight
    join_count = 0

    async def tracked_join():
        nonlocal join_count
        flight = await original_join()
        join_count += 1
        if join_count == expected_count:
            joined.set()
        return flight

    catalog._join_list_models_flight = tracked_join
    return joined


def _pending_list_discovery_tasks():
    return [
        task
        for task in asyncio.all_tasks()
        if not task.done()
        and task.get_name() == "model-catalog-list-models-discovery"
    ]


@pytest.mark.asyncio
async def test_list_discovery_deadline_expires_generically_and_clears_flight():
    started = asyncio.Event()
    cancelled = asyncio.Event()
    never_release = asyncio.Event()

    async def discover_models(*, reference_selector=None):
        assert reference_selector is None
        started.set()
        try:
            await never_release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    runtime = _runtime_with_discovery("local-runtime", discover_models)
    catalog = ModelCatalog(
        (runtime,),
        max_list_discovery_seconds=0.01,
    )

    caller = asyncio.create_task(catalog.list_models())
    await started.wait()
    with pytest.raises(ModelRuntimeUnavailableError) as captured:
        await caller

    assert cancelled.is_set() is True
    assert str(captured.value) == "local model discovery is unavailable"
    assert "0.01" not in str(captured.value)
    assert "local-runtime" not in str(captured.value)
    assert catalog._list_models_flight is None
    assert _pending_list_discovery_tasks() == []


@pytest.mark.asyncio
async def test_overlapping_and_late_waiters_share_one_discovery_deadline(
    monkeypatch,
):
    started = asyncio.Event()
    never_release = asyncio.Event()
    timeout_deadlines: list[float] = []
    original_timeout_at = asyncio.timeout_at

    def tracked_timeout_at(deadline):
        timeout_deadlines.append(deadline)
        return original_timeout_at(deadline)

    monkeypatch.setattr(
        catalog_module.asyncio,
        "timeout_at",
        tracked_timeout_at,
    )

    async def discover_models(*, reference_selector=None):
        assert reference_selector is None
        started.set()
        await never_release.wait()

    runtime = _runtime_with_discovery("local-runtime", discover_models)
    catalog = ModelCatalog(
        (runtime,),
        max_list_discovery_seconds=0.01,
    )
    joined = _signal_when_list_waiters_join(catalog, 2)

    caller_a = asyncio.create_task(catalog.list_models())
    await started.wait()
    caller_b = asyncio.create_task(catalog.list_models())
    await joined.wait()
    failures = await asyncio.gather(
        caller_a,
        caller_b,
        return_exceptions=True,
    )

    assert len(timeout_deadlines) == 1
    assert runtime.discover_models.await_count == 1
    assert isinstance(failures[0], ModelRuntimeUnavailableError)
    assert failures[0] is failures[1]
    assert catalog._list_models_flight is None
    assert _pending_list_discovery_tasks() == []


@pytest.mark.asyncio
async def test_timed_out_list_flight_is_not_retained_and_retry_can_succeed():
    started = asyncio.Event()
    never_release = asyncio.Event()
    invocation = 0
    models = (RuntimeModel(reference="model-tag", display_name="Model"),)

    async def discover_models(*, reference_selector=None):
        nonlocal invocation
        assert reference_selector is None
        invocation += 1
        if invocation == 1:
            started.set()
            await never_release.wait()
        return models

    runtime = _runtime_with_discovery("local-runtime", discover_models)
    catalog = ModelCatalog(
        (runtime,),
        max_list_discovery_seconds=0.01,
    )

    first = asyncio.create_task(catalog.list_models())
    await started.wait()
    with pytest.raises(ModelRuntimeUnavailableError):
        await first
    assert catalog._list_models_flight is None

    retry = await catalog.list_models()

    assert retry[0].display_name == "Model"
    assert runtime.discover_models.await_count == 2
    assert catalog._list_models_flight is None
    assert _pending_list_discovery_tasks() == []


@pytest.mark.asyncio
async def test_one_deadline_governs_multiple_runtime_discoveries():
    second_started = asyncio.Event()
    never_release = asyncio.Event()
    first_runtime = _runtime(
        "runtime-one",
        (RuntimeModel(reference="one", display_name="One"),),
    )

    async def second_discovery(*, reference_selector=None):
        assert reference_selector is None
        second_started.set()
        await never_release.wait()

    second_runtime = _runtime_with_discovery(
        "runtime-two",
        second_discovery,
    )
    catalog = ModelCatalog(
        (first_runtime, second_runtime),
        max_list_discovery_seconds=0.01,
    )

    caller = asyncio.create_task(catalog.list_models())
    await second_started.wait()
    with pytest.raises(ModelRuntimeUnavailableError):
        await caller

    first_runtime.discover_models.assert_awaited_once_with()
    second_runtime.discover_models.assert_awaited_once_with()
    assert catalog._list_models_flight is None
    assert _pending_list_discovery_tasks() == []


@pytest.mark.asyncio
async def test_targeted_resolution_is_independent_from_list_deadline():
    model = RuntimeModel(
        reference="selected-tag",
        display_name="Selected",
        capabilities=(ModelCapability.TEXT_GENERATION,),
    )
    source_runtime = _runtime("local-runtime", (model,))
    (descriptor,) = await ModelCatalog((source_runtime,)).list_models()
    list_started = asyncio.Event()
    never_release = asyncio.Event()

    async def discover_models(*, reference_selector=None):
        if reference_selector is None:
            list_started.set()
            await never_release.wait()
        return tuple(
            candidate
            for candidate in (model,)
            if reference_selector is not None
            and reference_selector(candidate.reference)
        )

    runtime = _runtime_with_discovery("local-runtime", discover_models)
    catalog = ModelCatalog(
        (runtime,),
        max_list_discovery_seconds=0.01,
    )
    list_caller = asyncio.create_task(catalog.list_models())
    await list_started.wait()

    resolved = await catalog.resolve_model(descriptor.model_id)

    assert resolved is not None
    assert resolved.descriptor == descriptor
    with pytest.raises(ModelRuntimeUnavailableError):
        await list_caller
    assert runtime.discover_models.await_count == 2
    assert catalog._list_models_flight is None
    assert _pending_list_discovery_tasks() == []


@pytest.mark.asyncio
async def test_final_monotonic_check_rejects_synchronous_deadline_overrun():
    async def discover_models(*, reference_selector=None):
        assert reference_selector is None
        loop = asyncio.get_running_loop()
        stop_at = loop.time() + 0.002
        while loop.time() < stop_at:
            pass
        return (RuntimeModel(reference="model-tag", display_name="Model"),)

    runtime = _runtime_with_discovery("local-runtime", discover_models)
    catalog = ModelCatalog(
        (runtime,),
        max_list_discovery_seconds=0.001,
    )

    with pytest.raises(ModelRuntimeUnavailableError):
        await catalog.list_models()

    assert catalog._list_models_flight is None
    assert _pending_list_discovery_tasks() == []


@pytest.mark.asyncio
async def test_two_overlapping_list_callers_share_one_discovery():
    started = asyncio.Event()
    release = asyncio.Event()
    models = (
        RuntimeModel(reference="z-tag", display_name="zeta"),
        RuntimeModel(reference="a-tag", display_name="Alpha"),
    )

    async def discover_models(*, reference_selector=None):
        assert reference_selector is None
        started.set()
        await release.wait()
        return models

    runtime = _runtime_with_discovery("local-runtime", discover_models)
    catalog = ModelCatalog((runtime,))
    joined = _signal_when_list_waiters_join(catalog, 2)

    caller_a = asyncio.create_task(catalog.list_models())
    await started.wait()
    caller_b = asyncio.create_task(catalog.list_models())
    await joined.wait()

    assert runtime.discover_models.await_count == 1
    release.set()
    result_a, result_b = await asyncio.gather(caller_a, caller_b)

    assert result_a is result_b
    assert [model.display_name for model in result_a] == ["Alpha", "zeta"]
    assert catalog._list_models_flight is None
    assert _pending_list_discovery_tasks() == []


@pytest.mark.asyncio
async def test_many_overlapping_list_callers_share_one_discovery():
    started = asyncio.Event()
    release = asyncio.Event()
    models = (RuntimeModel(reference="model-tag", display_name="Model"),)

    async def discover_models(*, reference_selector=None):
        assert reference_selector is None
        started.set()
        await release.wait()
        return models

    runtime = _runtime_with_discovery("local-runtime", discover_models)
    catalog = ModelCatalog((runtime,))
    caller_count = 4
    joined = _signal_when_list_waiters_join(catalog, caller_count)

    callers = [
        asyncio.create_task(catalog.list_models())
        for _ in range(caller_count)
    ]
    await started.wait()
    await joined.wait()

    assert runtime.discover_models.await_count == 1
    release.set()
    results = await asyncio.gather(*callers)

    assert all(result is results[0] for result in results)
    assert runtime.discover_models.await_count == 1
    assert catalog._list_models_flight is None
    assert _pending_list_discovery_tasks() == []


@pytest.mark.asyncio
async def test_sequential_list_calls_are_fresh_and_never_cached():
    invocation = 0

    async def discover_models(*, reference_selector=None):
        nonlocal invocation
        assert reference_selector is None
        invocation += 1
        return (
            RuntimeModel(
                reference="model-tag",
                display_name=f"Model {invocation}",
            ),
        )

    runtime = _runtime_with_discovery("local-runtime", discover_models)
    catalog = ModelCatalog((runtime,))

    first = await catalog.list_models()
    assert catalog._list_models_flight is None
    second = await catalog.list_models()

    assert first[0].display_name == "Model 1"
    assert second[0].display_name == "Model 2"
    assert runtime.discover_models.await_count == 2
    assert catalog._list_models_flight is None
    assert _pending_list_discovery_tasks() == []


@pytest.mark.asyncio
async def test_caller_after_completed_flight_starts_fresh_during_waiter_cleanup():
    first_started = asyncio.Event()
    first_release = asyncio.Event()
    second_started = asyncio.Event()
    second_release = asyncio.Event()
    first_leave_started = asyncio.Event()
    finish_first_leave = asyncio.Event()
    invocation = 0

    async def discover_models(*, reference_selector=None):
        nonlocal invocation
        assert reference_selector is None
        invocation += 1
        if invocation == 1:
            first_started.set()
            await first_release.wait()
        else:
            second_started.set()
            await second_release.wait()
        return (
            RuntimeModel(
                reference=f"model-{invocation}",
                display_name=f"Model {invocation}",
            ),
        )

    runtime = _runtime_with_discovery("local-runtime", discover_models)
    catalog = ModelCatalog((runtime,))
    original_leave = catalog._leave_list_models_flight
    leave_count = 0

    async def delayed_first_leave(flight):
        nonlocal leave_count
        leave_count += 1
        if leave_count == 1:
            first_leave_started.set()
            await finish_first_leave.wait()
        await original_leave(flight)

    catalog._leave_list_models_flight = delayed_first_leave

    first_caller = asyncio.create_task(catalog.list_models())
    await first_started.wait()
    first_release.set()
    await first_leave_started.wait()

    assert catalog._list_models_flight is None
    second_caller = asyncio.create_task(catalog.list_models())
    await second_started.wait()
    assert runtime.discover_models.await_count == 2
    second_release.set()
    second = await second_caller

    finish_first_leave.set()
    first = await first_caller

    assert first[0].display_name == "Model 1"
    assert second[0].display_name == "Model 2"
    assert catalog._list_models_flight is None
    assert _pending_list_discovery_tasks() == []


@pytest.mark.asyncio
async def test_shared_list_flight_discovers_each_runtime_once():
    first_started = asyncio.Event()
    first_release = asyncio.Event()

    async def discover_first(*, reference_selector=None):
        assert reference_selector is None
        first_started.set()
        await first_release.wait()
        return (RuntimeModel(reference="z-tag", display_name="zeta"),)

    async def discover_second(*, reference_selector=None):
        assert reference_selector is None
        return (RuntimeModel(reference="a-tag", display_name="Alpha"),)

    first_runtime = _runtime_with_discovery("runtime-one", discover_first)
    second_runtime = _runtime_with_discovery("runtime-two", discover_second)
    catalog = ModelCatalog((first_runtime, second_runtime))
    joined = _signal_when_list_waiters_join(catalog, 2)

    caller_a = asyncio.create_task(catalog.list_models())
    await first_started.wait()
    caller_b = asyncio.create_task(catalog.list_models())
    await joined.wait()
    first_release.set()
    result_a, result_b = await asyncio.gather(caller_a, caller_b)

    assert result_a is result_b
    assert [model.display_name for model in result_a] == ["Alpha", "zeta"]
    assert [model.runtime_id for model in result_a] == [
        "runtime-two",
        "runtime-one",
    ]
    first_runtime.discover_models.assert_awaited_once_with()
    second_runtime.discover_models.assert_awaited_once_with()
    assert all(len(model.model_id.split(":", 1)[1]) == 24 for model in result_a)
    assert catalog._list_models_flight is None


@pytest.mark.asyncio
async def test_shared_list_failure_is_not_retained_and_retry_can_succeed():
    started = asyncio.Event()
    release = asyncio.Event()
    failure = ModelRuntimeUnavailableError("runtime unavailable")
    invocation = 0

    async def discover_models(*, reference_selector=None):
        nonlocal invocation
        assert reference_selector is None
        invocation += 1
        if invocation == 1:
            started.set()
            await release.wait()
            raise failure
        return (RuntimeModel(reference="model-tag", display_name="Model"),)

    runtime = _runtime_with_discovery("local-runtime", discover_models)
    catalog = ModelCatalog((runtime,))
    joined = _signal_when_list_waiters_join(catalog, 2)

    caller_a = asyncio.create_task(catalog.list_models())
    await started.wait()
    caller_b = asyncio.create_task(catalog.list_models())
    await joined.wait()
    release.set()
    failures = await asyncio.gather(
        caller_a,
        caller_b,
        return_exceptions=True,
    )

    assert failures[0] is failure
    assert failures[1] is failure
    assert catalog._list_models_flight is None

    retry = await catalog.list_models()

    assert retry[0].display_name == "Model"
    assert runtime.discover_models.await_count == 2
    assert catalog._list_models_flight is None
    assert _pending_list_discovery_tasks() == []


@pytest.mark.asyncio
async def test_cancelling_one_list_waiter_does_not_cancel_shared_discovery():
    started = asyncio.Event()
    release = asyncio.Event()
    discovery_cancelled = asyncio.Event()
    models = (RuntimeModel(reference="model-tag", display_name="Model"),)

    async def discover_models(*, reference_selector=None):
        assert reference_selector is None
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            discovery_cancelled.set()
            raise
        return models

    runtime = _runtime_with_discovery("local-runtime", discover_models)
    catalog = ModelCatalog((runtime,))
    joined = _signal_when_list_waiters_join(catalog, 2)

    caller_a = asyncio.create_task(catalog.list_models())
    await started.wait()
    caller_b = asyncio.create_task(catalog.list_models())
    await joined.wait()

    caller_a.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller_a

    assert discovery_cancelled.is_set() is False
    assert catalog._list_models_flight is not None
    assert catalog._list_models_flight.waiter_count == 1

    release.set()
    result_b = await caller_b

    assert result_b[0].display_name == "Model"
    assert runtime.discover_models.await_count == 1
    assert discovery_cancelled.is_set() is False
    assert catalog._list_models_flight is None
    assert _pending_list_discovery_tasks() == []


@pytest.mark.asyncio
async def test_cancelling_last_list_waiter_cancels_and_awaits_discovery():
    started = asyncio.Event()
    never_release = asyncio.Event()
    discovery_cancelled = asyncio.Event()
    finish_cancellation_cleanup = asyncio.Event()
    invocation = 0
    models = (RuntimeModel(reference="model-tag", display_name="Model"),)

    async def discover_models(*, reference_selector=None):
        nonlocal invocation
        assert reference_selector is None
        invocation += 1
        if invocation == 1:
            started.set()
            try:
                await never_release.wait()
            except asyncio.CancelledError:
                discovery_cancelled.set()
                await finish_cancellation_cleanup.wait()
                raise
        return models

    runtime = _runtime_with_discovery("local-runtime", discover_models)
    catalog = ModelCatalog((runtime,))

    caller = asyncio.create_task(catalog.list_models())
    await started.wait()
    caller.cancel()
    await discovery_cancelled.wait()

    assert caller.done() is False
    assert catalog._list_models_flight is not None
    assert catalog._list_models_flight.waiter_count == 0

    finish_cancellation_cleanup.set()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert catalog._list_models_flight is None

    assert _pending_list_discovery_tasks() == []
    retry = await catalog.list_models()
    assert retry[0].display_name == "Model"
    assert runtime.discover_models.await_count == 2
    assert catalog._list_models_flight is None


@pytest.mark.asyncio
async def test_final_cancellation_after_another_waiter_cancels_shared_discovery():
    started = asyncio.Event()
    never_release = asyncio.Event()
    discovery_cancelled = asyncio.Event()
    finish_cancellation_cleanup = asyncio.Event()
    models = (RuntimeModel(reference="model-tag", display_name="Model"),)

    async def discover_models(*, reference_selector=None):
        assert reference_selector is None
        started.set()
        try:
            await never_release.wait()
        except asyncio.CancelledError:
            discovery_cancelled.set()
            await finish_cancellation_cleanup.wait()
            raise
        return models

    runtime = _runtime_with_discovery("local-runtime", discover_models)
    catalog = ModelCatalog((runtime,))
    joined = _signal_when_list_waiters_join(catalog, 2)

    caller_a = asyncio.create_task(catalog.list_models())
    await started.wait()
    caller_b = asyncio.create_task(catalog.list_models())
    await joined.wait()

    caller_a.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller_a
    assert discovery_cancelled.is_set() is False

    caller_b.cancel()
    await discovery_cancelled.wait()
    assert caller_b.done() is False
    assert catalog._list_models_flight is not None
    assert catalog._list_models_flight.waiter_count == 0

    finish_cancellation_cleanup.set()
    with pytest.raises(asyncio.CancelledError):
        await caller_b

    assert runtime.discover_models.await_count == 1
    assert catalog._list_models_flight is None
    assert _pending_list_discovery_tasks() == []


@pytest.mark.asyncio
async def test_completed_flight_is_not_retained_during_waiter_cancellation():
    discovery_started = asyncio.Event()
    release_discovery = asyncio.Event()
    waiter_cleanup_started = asyncio.Event()
    hold_waiter_cleanup = asyncio.Event()
    invocation = 0
    models = (RuntimeModel(reference="model-tag", display_name="Model"),)

    async def discover_models(*, reference_selector=None):
        nonlocal invocation
        assert reference_selector is None
        invocation += 1
        if invocation == 1:
            discovery_started.set()
            await release_discovery.wait()
        return models

    runtime = _runtime_with_discovery("local-runtime", discover_models)
    catalog = ModelCatalog((runtime,))
    original_leave = catalog._leave_list_models_flight
    first_leave = True

    async def cancellable_leave(flight):
        nonlocal first_leave
        if first_leave:
            first_leave = False
            waiter_cleanup_started.set()
            try:
                await hold_waiter_cleanup.wait()
            finally:
                await original_leave(flight)
            return
        await original_leave(flight)

    catalog._leave_list_models_flight = cancellable_leave

    caller = asyncio.create_task(catalog.list_models())
    await discovery_started.wait()
    release_discovery.set()
    await waiter_cleanup_started.wait()

    assert catalog._list_models_flight is None
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller

    assert catalog._list_models_flight is None
    assert _pending_list_discovery_tasks() == []

    retry = await catalog.list_models()
    assert retry[0].display_name == "Model"
    assert runtime.discover_models.await_count == 2
    assert catalog._list_models_flight is None


@pytest.mark.asyncio
async def test_resolve_model_remains_independent_from_active_list_flight():
    reference = "/private/runtime/selected:14b"
    model = RuntimeModel(
        reference=reference,
        display_name="Selected 14B",
        capabilities=(ModelCapability.TEXT_GENERATION,),
    )
    source_runtime = _runtime("local-runtime", (model,))
    (descriptor,) = await ModelCatalog((source_runtime,)).list_models()

    list_started = asyncio.Event()
    release_list = asyncio.Event()
    targeted_finished = asyncio.Event()

    async def discover_models(*, reference_selector=None):
        if reference_selector is None:
            list_started.set()
            await release_list.wait()
            return (model,)
        targeted_finished.set()
        return tuple(
            candidate
            for candidate in (model,)
            if reference_selector(candidate.reference)
        )

    runtime = _runtime_with_discovery("local-runtime", discover_models)
    catalog = ModelCatalog((runtime,))
    list_caller = asyncio.create_task(catalog.list_models())
    await list_started.wait()

    resolved = await catalog.resolve_model(descriptor.model_id)

    assert targeted_finished.is_set() is True
    assert resolved is not None
    assert resolved.descriptor == descriptor
    assert resolved.runtime_reference == reference
    assert list_caller.done() is False
    assert catalog._list_models_flight is not None
    assert runtime.discover_models.await_count == 2
    assert runtime.discover_models.await_args_list[0] == call()
    selector = runtime.discover_models.await_args_list[1].kwargs[
        "reference_selector"
    ]
    assert selector(reference) is True

    release_list.set()
    listed = await list_caller

    assert listed == (descriptor,)
    assert catalog._list_models_flight is None
    assert _pending_list_discovery_tasks() == []

@pytest.mark.asyncio
async def test_overlapping_model_list_api_requests_share_one_full_discovery():
    started = asyncio.Event()
    release = asyncio.Event()
    models = (
        RuntimeModel(reference="z-tag", display_name="Zeta"),
        RuntimeModel(
            reference="a-tag",
            display_name="Alpha",
            capabilities=(ModelCapability.TEXT_GENERATION,),
        ),
    )

    async def discover_models(*, reference_selector=None):
        assert reference_selector is None
        started.set()
        await release.wait()
        return models

    runtime = _runtime_with_discovery("local-runtime", discover_models)
    catalog = ModelCatalog((runtime,))
    joined = _signal_when_list_waiters_join(catalog, 2)
    api = FastAPI()
    api.include_router(ai_router, prefix="/api/v1")
    api.state.model_catalog = catalog
    api.state.model_list_max_response_bytes = 1_048_576

    async def override_current_user():
        return object()

    api.dependency_overrides[get_current_user] = override_current_user
    transport = httpx.ASGITransport(app=api)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        caller_a = asyncio.create_task(client.get("/api/v1/ai/models"))
        await started.wait()
        caller_b = asyncio.create_task(client.get("/api/v1/ai/models"))
        await joined.wait()

        assert runtime.discover_models.await_count == 1
        release.set()
        response_a, response_b = await asyncio.gather(caller_a, caller_b)

    assert response_a.status_code == 200
    assert response_b.status_code == 200
    assert response_a.json() == response_b.json()
    assert [item["display_name"] for item in response_a.json()["items"]] == [
        "Alpha",
        "Zeta",
    ]
    assert runtime.discover_models.await_count == 1
    assert catalog._list_models_flight is None
    assert _pending_list_discovery_tasks() == []


@pytest.mark.asyncio
async def test_overlapping_model_list_api_requests_share_generic_timeout(
    monkeypatch,
):
    started = asyncio.Event()
    never_release = asyncio.Event()

    async def discover_models(*, reference_selector=None):
        assert reference_selector is None
        started.set()
        await never_release.wait()

    runtime = _runtime_with_discovery("local-runtime", discover_models)
    catalog = ModelCatalog(
        (runtime,),
        max_list_discovery_seconds=0.01,
    )
    joined = _signal_when_list_waiters_join(catalog, 2)
    response_accounting = Mock(
        wraps=ai_module._model_list_response_json_size
    )
    monkeypatch.setattr(
        ai_module,
        "_model_list_response_json_size",
        response_accounting,
    )
    api = FastAPI()
    register_exception_handlers(api)
    api.include_router(ai_router, prefix="/api/v1")
    api.state.model_catalog = catalog
    api.state.model_list_max_response_bytes = 1_048_576

    async def override_current_user():
        return object()

    api.dependency_overrides[get_current_user] = override_current_user
    transport = httpx.ASGITransport(app=api)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        caller_a = asyncio.create_task(client.get("/api/v1/ai/models"))
        await started.wait()
        caller_b = asyncio.create_task(client.get("/api/v1/ai/models"))
        await joined.wait()
        response_a, response_b = await asyncio.gather(caller_a, caller_b)

    for response in (response_a, response_b):
        assert response.status_code == 503
        assert response.json()["error"] == {
            "code": "HTTP_ERROR",
            "message": "Local model runtime unavailable",
        }
        assert "local-runtime" not in response.text
    assert runtime.discover_models.await_count == 1
    response_accounting.assert_not_called()
    assert catalog._list_models_flight is None
    assert _pending_list_discovery_tasks() == []
