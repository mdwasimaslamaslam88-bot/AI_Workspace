from unittest.mock import AsyncMock, Mock, call

import pytest

from app.ai.catalog import (
    ModelAvailability,
    ModelCapability,
    ModelCatalog,
    ModelDescriptor,
    ModelModality,
    RuntimeModel,
)


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

    resolved = await ModelCatalog((runtime,)).resolve_model(
        f"other-runtime:{'a' * 24}"
    )

    assert resolved is None
    runtime.discover_models.assert_not_awaited()


@pytest.mark.parametrize(
    "model_id",
    ["", "raw-tag", "LOCAL:" + "a" * 24, "local:short"],
)
@pytest.mark.asyncio
async def test_catalog_rejects_invalid_public_id_before_discovery(model_id):
    runtime = _runtime("local-runtime", ())

    with pytest.raises(ValueError, match="runtime-namespaced"):
        await ModelCatalog((runtime,)).resolve_model(model_id)

    runtime.discover_models.assert_not_awaited()
