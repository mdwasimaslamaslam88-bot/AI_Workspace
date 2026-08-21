import asyncio
import json
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

import app.clients.ollama as ollama_client_module
import app.runtimes.ollama as ollama_runtime_module
from app.ai.catalog import (
    ModelCapability,
    ModelCatalog,
    ModelRuntimeUnavailableError,
)
from app.ai.generation import (
    TextGenerationMessage,
    TextGenerationRequestTooLargeError,
    TextGenerationRole,
    TextGenerationRuntimeUnavailableError,
    TextGenerationRuntimeUnsupportedError,
)
from app.clients.ollama import check_ollama, create_ollama_client
from app.core.config import (
    MAX_OLLAMA_CATALOG_LIST_MODELS,
    MAX_OLLAMA_CATALOG_RESPONSE_BYTES,
    MAX_OLLAMA_GENERATION_REQUEST_BYTES,
    MAX_OLLAMA_GENERATION_RESPONSE_BYTES,
    Settings,
)
from app.runtimes.ollama import (
    OllamaModelDiscoveryRuntime,
    OllamaTextGenerationRuntime,
)

LOCAL_MODEL_REFERENCE = "/private/runtime/model:14b"
LOCAL_MODEL_ALLOWLIST = (LOCAL_MODEL_REFERENCE,)
DUPLICATE_MODEL_REFERENCE = "/private/runtime/duplicate:14b"


class _CatalogRecordingStream(httpx.AsyncByteStream):
    def __init__(
        self,
        chunks=(),
        *,
        blocked: asyncio.Event | None = None,
        release: asyncio.Event | None = None,
        failure: Exception | None = None,
        close_started: asyncio.Event | None = None,
        close_release: asyncio.Event | None = None,
    ):
        self.chunks = tuple(chunks)
        self.blocked = blocked
        self.release = release
        self.failure = failure
        self.close_started = close_started
        self.close_release = close_release
        self.iteration_count = 0
        self.closed = False

    async def __aiter__(self):
        for index, chunk in enumerate(self.chunks):
            self.iteration_count += 1
            yield chunk
            if index == 0 and self.blocked is not None:
                self.blocked.set()
                if self.release is None:
                    raise AssertionError("blocked stream requires a release event")
                await self.release.wait()
        if self.failure is not None:
            raise self.failure

    async def aclose(self):
        self.closed = True
        if self.close_started is not None:
            self.close_started.set()
            if self.close_release is None:
                raise AssertionError("blocked close requires a release event")
            await self.close_release.wait()


def _catalog_json_body(payload) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode()


def _catalog_json_body_at_size(payload, size: int) -> bytes:
    padded = dict(payload)
    padded["ignored_padding"] = ""
    empty = _catalog_json_body(padded)
    padding_size = size - len(empty)
    if padding_size < 0:
        raise ValueError("test catalog response size is too small")
    padded["ignored_padding"] = "x" * padding_size
    body = _catalog_json_body(padded)
    assert len(body) == size
    return body


def _catalog_response(
    stream: _CatalogRecordingStream,
    *,
    status_code: int = 200,
    headers=(),
) -> httpx.Response:
    return httpx.Response(
        status_code,
        headers=list(headers),
        stream=stream,
    )


async def _assert_duplicate_inventory_rejected_before_show(
    inventory: list[dict[str, object]],
    allowlist: tuple[str, ...],
    *,
    reference_selector=None,
    private_values: tuple[str, ...] = (),
) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/show":
            raise AssertionError("duplicate inventory must prevent detail requests")
        return httpx.Response(200, json={"models": inventory})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        runtime = OllamaModelDiscoveryRuntime(
            client,
            allowlist,
        )
        with pytest.raises(ModelRuntimeUnavailableError) as captured:
            if reference_selector is None:
                await runtime.discover_models()
            else:
                await runtime.discover_models(
                    reference_selector=reference_selector
                )

    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/api/tags")
    ]
    assert str(captured.value) == (
        "local model runtime returned an invalid inventory"
    )
    assert captured.value.__cause__ is None
    for private_value in private_values:
        assert private_value not in str(captured.value)


@pytest.mark.asyncio
async def test_ollama_discovery_uses_documented_capabilities_and_hides_reference():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/show":
            return httpx.Response(
                200,
                json={
                    "capabilities": [
                        "vision",
                        "completion",
                        "embedding",
                        "tools",
                        "completion",
                        "thinking",
                        "unknown-capability",
                    ],
                    "template": "must not infer chat",
                    "parameters": "must not infer settings",
                    "license": "must not become public metadata",
                    "model_info": {"private.path": "/private/model"},
                },
            )
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "/private/models/secret:14b",
                        "details": {
                            "family": "LocalFamily",
                            "parameter_size": "14B",
                            "quantization_level": "Q4_K_M",
                        },
                        "capabilities": [
                            "CHAT",
                            "text-generation",
                            "chat",
                            "unknown-capability",
                        ],
                    }
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
        trust_env=False,
        follow_redirects=False,
    ) as client:
        (model,) = await OllamaModelDiscoveryRuntime(
            client,
            ("/private/models/secret:14b",),
        ).discover_models()

    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/api/tags"),
        ("POST", "/api/show"),
    ]
    assert json.loads(requests[1].content) == {
        "model": "/private/models/secret:14b"
    }
    assert model.reference == "/private/models/secret:14b"
    assert model.display_name == "LocalFamily 14B"
    assert model.family == "LocalFamily"
    assert model.parameter_class == "14B"
    assert model.quantization == "Q4_K_M"
    assert model.capabilities == (
        ModelCapability.EMBEDDINGS,
        ModelCapability.TEXT_GENERATION,
        ModelCapability.TOOL_CALLING,
        ModelCapability.VISION_INPUT,
    )


@pytest.mark.asyncio
async def test_ollama_duplicate_first_fails_before_any_show_request():
    later_reference = "/private/runtime/later:7b"
    await _assert_duplicate_inventory_rejected_before_show(
        [
            {
                "model": DUPLICATE_MODEL_REFERENCE,
                "details": {"family": "private-first-family"},
            },
            {
                "model": DUPLICATE_MODEL_REFERENCE,
                "details": {"family": "private-second-family"},
            },
            {"model": later_reference, "details": {}},
        ],
        (DUPLICATE_MODEL_REFERENCE, later_reference),
        private_values=(
            DUPLICATE_MODEL_REFERENCE,
            "private-first-family",
            "private-second-family",
        ),
    )


@pytest.mark.asyncio
async def test_ollama_duplicate_after_unique_entries_prevents_all_show_requests():
    references = (
        "/private/runtime/unique-one:7b",
        "/private/runtime/unique-two:14b",
        "/private/runtime/unique-three:32b",
    )
    await _assert_duplicate_inventory_rejected_before_show(
        [
            {"model": references[0], "details": {"family": "FamilyOne"}},
            {"model": references[1], "details": {"family": "FamilyTwo"}},
            {"model": references[2], "details": {"family": "FamilyThree"}},
            {
                "model": references[0],
                "details": {"family": "private-late-duplicate"},
            },
        ],
        references,
        private_values=(references[0], "private-late-duplicate"),
    )


@pytest.mark.asyncio
async def test_ollama_duplicate_with_conflicting_metadata_fails_closed():
    await _assert_duplicate_inventory_rejected_before_show(
        [
            {
                "model": DUPLICATE_MODEL_REFERENCE,
                "details": {
                    "family": "private-first-family",
                    "parameter_size": "7B",
                    "quantization_level": "Q4_K_M",
                },
            },
            {
                "model": DUPLICATE_MODEL_REFERENCE,
                "details": {
                    "family": "private-last-family",
                    "parameter_size": "70B",
                    "quantization_level": "Q8_0",
                },
            },
        ],
        (DUPLICATE_MODEL_REFERENCE,),
        private_values=(
            DUPLICATE_MODEL_REFERENCE,
            "private-first-family",
            "private-last-family",
            "Q4_K_M",
            "Q8_0",
        ),
    )


@pytest.mark.asyncio
async def test_ollama_many_duplicate_entries_fail_before_any_show_request():
    await _assert_duplicate_inventory_rejected_before_show(
        [
            {
                "model": DUPLICATE_MODEL_REFERENCE,
                "details": {"family": f"private-family-{index}"},
            }
            for index in range(128)
        ],
        (DUPLICATE_MODEL_REFERENCE,),
        private_values=(DUPLICATE_MODEL_REFERENCE, "private-family-127"),
    )


@pytest.mark.asyncio
async def test_ollama_duplicate_nonallowlisted_entries_remain_ignored():
    ignored_reference = "/private/runtime/not-approved:70b"
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion"]})
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "model": ignored_reference,
                        "details": {"family": "IgnoredOne"},
                    },
                    {
                        "model": ignored_reference,
                        "details": "ignored malformed metadata",
                    },
                    {
                        "model": LOCAL_MODEL_REFERENCE,
                        "details": {"family": "VerifiedLocal"},
                    },
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        (model,) = await OllamaModelDiscoveryRuntime(
            client,
            LOCAL_MODEL_ALLOWLIST,
        ).discover_models()

    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/api/tags"),
        ("POST", "/api/show"),
    ]
    assert json.loads(requests[1].content) == {"model": LOCAL_MODEL_REFERENCE}
    assert model.reference == LOCAL_MODEL_REFERENCE
    assert model.family == "VerifiedLocal"
    assert model.capabilities == (ModelCapability.TEXT_GENERATION,)


@pytest.mark.asyncio
async def test_ollama_unique_allowlisted_entries_preserve_order_and_detail_calls():
    inventory_order = (
        "/private/runtime/third:32b",
        "/private/runtime/first:7b",
        "/private/runtime/second:14b",
    )
    allowlist = (
        "/private/runtime/first:7b",
        "/private/runtime/second:14b",
        "/private/runtime/third:32b",
    )
    show_references: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            show_references.append(json.loads(request.content)["model"])
            return httpx.Response(
                200,
                json={
                    "capabilities": [
                        "tools",
                        "completion",
                        "completion",
                        "unknown-capability",
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "model": reference,
                        "details": {
                            "family": f"Family{index}",
                            "parameter_size": f"{index}B",
                            "quantization_level": f"Q{index}",
                        },
                    }
                    for index, reference in enumerate(inventory_order, start=1)
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        models = await OllamaModelDiscoveryRuntime(
            client,
            allowlist,
        ).discover_models()

    assert tuple(model.reference for model in models) == inventory_order
    assert show_references == list(inventory_order)
    assert tuple(model.family for model in models) == (
        "Family1",
        "Family2",
        "Family3",
    )
    assert tuple(model.parameter_class for model in models) == ("1B", "2B", "3B")
    assert tuple(model.quantization for model in models) == ("Q1", "Q2", "Q3")
    assert all(
        model.capabilities
        == (
            ModelCapability.TEXT_GENERATION,
            ModelCapability.TOOL_CALLING,
        )
        for model in models
    )


@pytest.mark.parametrize(
    "model_count",
    [0, 1, 23, MAX_OLLAMA_CATALOG_LIST_MODELS],
    ids=["empty", "one", "representative", "exact-limit"],
)
@pytest.mark.asyncio
async def test_ollama_full_discovery_accepts_matching_count_at_or_below_limit(
    model_count,
):
    references = tuple(
        f"/private/runtime/list-model-{index}:latest"
        for index in range(model_count)
    )
    show_references: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            reference = json.loads(request.content)["model"]
            show_references.append(reference)
            return httpx.Response(
                200,
                json={"capabilities": ["completion"]},
            )
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "model": reference,
                        "details": {
                            "family": f"Family{index}",
                            "parameter_size": f"{index + 1}B",
                            "quantization_level": "Q4_K_M",
                        },
                    }
                    for index, reference in enumerate(references)
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        models = await OllamaModelDiscoveryRuntime(
            client,
            references,
            max_list_models=MAX_OLLAMA_CATALOG_LIST_MODELS,
        ).discover_models()

    assert tuple(model.reference for model in models) == references
    assert show_references == list(references)
    assert tuple(model.family for model in models) == tuple(
        f"Family{index}" for index in range(model_count)
    )
    assert all(
        model.capabilities == (ModelCapability.TEXT_GENERATION,)
        for model in models
    )


@pytest.mark.parametrize(
    "model_count",
    [MAX_OLLAMA_CATALOG_LIST_MODELS + 1, 1_024],
    ids=["limit-plus-one-late", "large-overflow"],
)
@pytest.mark.asyncio
async def test_ollama_full_discovery_rejects_over_limit_before_any_show(
    model_count,
):
    references = tuple(
        f"/private/runtime/private-overflow-{index}:latest"
        for index in range(model_count)
    )
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/show":
            raise AssertionError("over-limit inventory must prevent detail requests")
        return httpx.Response(
            200,
            json={
                "models": [
                    {"model": reference, "details": {}}
                    for reference in references
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(ModelRuntimeUnavailableError) as captured:
            await OllamaModelDiscoveryRuntime(
                client,
                references,
            ).discover_models()

    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/api/tags")
    ]
    assert str(captured.value) == "local model runtime inventory is unavailable"
    assert captured.value.__cause__ is None
    assert references[0] not in str(captured.value)
    assert references[-1] not in str(captured.value)
    assert str(model_count) not in str(captured.value)
    assert str(MAX_OLLAMA_CATALOG_LIST_MODELS) not in str(captured.value)


@pytest.mark.asyncio
async def test_ollama_nonallowlisted_inventory_does_not_consume_list_limit():
    approved_references = (
        "/private/runtime/approved-a:latest",
        "/private/runtime/approved-b:latest",
    )
    ignored_entries = [
        {"model": f"ignored-{index}"}
        for index in range(20_000)
    ]
    inventory = [
        *ignored_entries[:10_000],
        {"model": approved_references[0], "details": {"family": "ApprovedA"}},
        *ignored_entries[10_000:],
        {"model": approved_references[1], "details": {"family": "ApprovedB"}},
    ]
    show_references: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            reference = json.loads(request.content)["model"]
            show_references.append(reference)
            return httpx.Response(
                200,
                json={"capabilities": ["completion"]},
            )
        return httpx.Response(200, json={"models": inventory})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        models = await OllamaModelDiscoveryRuntime(
            client,
            approved_references,
            max_list_models=2,
        ).discover_models()

    assert tuple(model.reference for model in models) == approved_references
    assert tuple(model.family for model in models) == ("ApprovedA", "ApprovedB")
    assert show_references == list(approved_references)


@pytest.mark.asyncio
async def test_ollama_duplicate_validation_precedes_full_list_count_rejection():
    references = tuple(
        f"/private/runtime/duplicate-precedence-{index}:latest"
        for index in range(MAX_OLLAMA_CATALOG_LIST_MODELS + 1)
    )
    await _assert_duplicate_inventory_rejected_before_show(
        [
            *[
                {"model": reference, "details": {}}
                for reference in references
            ],
            {
                "model": references[0],
                "details": {"family": "private-conflicting-duplicate"},
            },
        ],
        references,
        private_values=(
            references[0],
            "private-conflicting-duplicate",
        ),
    )


@pytest.mark.asyncio
async def test_ollama_targeted_discovery_bypasses_only_full_list_count():
    references = tuple(
        f"/private/runtime/targetable-{index}:latest"
        for index in range(300)
    )
    target_reference = references[278]
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/show":
            return httpx.Response(
                200,
                json={
                    "capabilities": ["tools", "completion", "completion"],
                },
            )
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "model": reference,
                        "details": {
                            "family": f"Family{index}",
                            "parameter_size": f"{index + 1}B",
                            "quantization_level": "Q4_K_M",
                        },
                    }
                    for index, reference in enumerate(references)
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        (model,) = await OllamaModelDiscoveryRuntime(
            client,
            references,
            max_list_models=1,
        ).discover_models(
            reference_selector=lambda reference: (
                reference == target_reference
            )
        )

    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/api/tags"),
        ("POST", "/api/show"),
    ]
    assert json.loads(requests[1].content) == {"model": target_reference}
    assert model.reference == target_reference
    assert model.family == "Family278"
    assert model.parameter_class == "279B"
    assert model.quantization == "Q4_K_M"
    assert model.capabilities == (
        ModelCapability.TEXT_GENERATION,
        ModelCapability.TOOL_CALLING,
    )


@pytest.mark.parametrize("target_kind", ["missing", "nonallowlisted"])
@pytest.mark.asyncio
async def test_ollama_over_limit_targeted_miss_skips_show(target_kind):
    references = tuple(
        f"/private/runtime/targeted-miss-{index}:latest"
        for index in range(300)
    )
    target_reference = f"/private/runtime/{target_kind}-target:latest"
    inventory_references = (
        references
        if target_kind == "missing"
        else (*references, target_reference)
    )
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/show":
            raise AssertionError("missing selected model must not request details")
        return httpx.Response(
            200,
            json={
                "models": [
                    {"model": reference, "details": {}}
                    for reference in inventory_references
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        models = await OllamaModelDiscoveryRuntime(
            client,
            references,
            max_list_models=1,
        ).discover_models(
            reference_selector=lambda reference: (
                reference == target_reference
            )
        )

    assert models == ()
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/api/tags")
    ]


@pytest.mark.asyncio
async def test_ollama_over_limit_targeted_duplicate_still_prevents_show():
    references = tuple(
        f"/private/runtime/targeted-duplicate-{index}:latest"
        for index in range(300)
    )
    await _assert_duplicate_inventory_rejected_before_show(
        [
            *[
                {"model": reference, "details": {}}
                for reference in references
            ],
            {
                "model": references[0],
                "details": {"family": "private-targeted-duplicate"},
            },
        ],
        references,
        reference_selector=lambda reference: reference == references[-1],
        private_values=(
            references[0],
            "private-targeted-duplicate",
        ),
    )


@pytest.mark.asyncio
async def test_ollama_over_limit_failure_is_shared_only_by_active_list_flight():
    over_limit_references = tuple(
        f"/private/runtime/single-flight-overflow-{index}:latest"
        for index in range(MAX_OLLAMA_CATALOG_LIST_MODELS + 1)
    )
    retry_reference = "/private/runtime/single-flight-retry:latest"
    tags_started = asyncio.Event()
    release_tags = asyncio.Event()
    two_waiters_joined = asyncio.Event()
    tags_count = 0
    show_references: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tags_count
        if request.url.path == "/api/tags":
            tags_count += 1
            if tags_count == 1:
                tags_started.set()
                await release_tags.wait()
                references = over_limit_references
            else:
                references = (retry_reference,)
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"model": reference, "details": {}}
                        for reference in references
                    ]
                },
            )
        reference = json.loads(request.content)["model"]
        show_references.append(reference)
        return httpx.Response(
            200,
            json={"capabilities": ["completion"]},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        catalog = ModelCatalog(
            (
                OllamaModelDiscoveryRuntime(
                    client,
                    (*over_limit_references, retry_reference),
                ),
            )
        )
        original_join = catalog._join_list_models_flight
        join_count = 0

        async def tracked_join():
            nonlocal join_count
            flight = await original_join()
            join_count += 1
            if join_count == 2:
                two_waiters_joined.set()
            return flight

        catalog._join_list_models_flight = tracked_join
        caller_a = asyncio.create_task(catalog.list_models())
        await tags_started.wait()
        caller_b = asyncio.create_task(catalog.list_models())
        await two_waiters_joined.wait()
        release_tags.set()
        failures = await asyncio.gather(
            caller_a,
            caller_b,
            return_exceptions=True,
        )

        assert isinstance(failures[0], ModelRuntimeUnavailableError)
        assert failures[0] is failures[1]
        assert str(failures[0]) == (
            "local model runtime inventory is unavailable"
        )
        assert catalog._list_models_flight is None
        assert show_references == []

        (retried,) = await catalog.list_models()

    assert tags_count == 2
    assert show_references == [retry_reference]
    assert retried.display_name == "Local text model"
    assert retried.capabilities == (ModelCapability.TEXT_GENERATION,)
    assert catalog._list_models_flight is None


@pytest.mark.parametrize(
    "target_index",
    [0, 1, 2],
    ids=["first", "middle", "last"],
)
@pytest.mark.asyncio
async def test_ollama_targeted_discovery_fetches_only_selected_detail(
    target_index,
):
    references = (
        "/private/runtime/first:7b",
        "/private/runtime/middle:14b",
        "/private/runtime/last:32b",
    )
    target_reference = references[target_index]
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/show":
            return httpx.Response(
                200,
                json={
                    "capabilities": [
                        "tools",
                        "completion",
                        "completion",
                        "unknown-capability",
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "model": reference,
                        "details": {
                            "family": f"Family{index}",
                            "parameter_size": f"{index}B",
                            "quantization_level": f"Q{index}",
                        },
                    }
                    for index, reference in enumerate(references, start=1)
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        (model,) = await OllamaModelDiscoveryRuntime(
            client,
            references,
        ).discover_models(
            reference_selector=lambda reference: (
                reference == target_reference
            )
        )

    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/api/tags"),
        ("POST", "/api/show"),
    ]
    assert json.loads(requests[1].content) == {"model": target_reference}
    assert model.reference == target_reference
    assert model.family == f"Family{target_index + 1}"
    assert model.parameter_class == f"{target_index + 1}B"
    assert model.quantization == f"Q{target_index + 1}"
    assert model.capabilities == (
        ModelCapability.TEXT_GENERATION,
        ModelCapability.TOOL_CALLING,
    )


@pytest.mark.parametrize(
    ("target_reference", "allowlist"),
    [
        pytest.param(
            "/private/runtime/absent:70b",
            (
                "/private/runtime/first:7b",
                "/private/runtime/middle:14b",
                "/private/runtime/last:32b",
            ),
            id="missing",
        ),
        pytest.param(
            "/private/runtime/last:32b",
            (
                "/private/runtime/first:7b",
                "/private/runtime/middle:14b",
            ),
            id="nonallowlisted",
        ),
    ],
)
@pytest.mark.asyncio
async def test_ollama_targeted_missing_or_nonallowlisted_model_skips_show(
    target_reference,
    allowlist,
):
    references = (
        "/private/runtime/first:7b",
        "/private/runtime/middle:14b",
        "/private/runtime/last:32b",
    )
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/show":
            raise AssertionError("missing target must not request details")
        return httpx.Response(
            200,
            json={
                "models": [
                    {"model": reference, "details": {}}
                    for reference in references
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        models = await OllamaModelDiscoveryRuntime(
            client,
            allowlist,
        ).discover_models(
            reference_selector=lambda reference: (
                reference == target_reference
            )
        )

    assert models == ()
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/api/tags")
    ]


@pytest.mark.asyncio
async def test_ollama_targeted_unrelated_late_duplicate_prevents_all_show():
    target_reference = "/private/runtime/target:14b"
    duplicate_reference = "/private/runtime/unrelated:7b"
    await _assert_duplicate_inventory_rejected_before_show(
        [
            {
                "model": target_reference,
                "details": {"family": "TargetFamily"},
            },
            {
                "model": duplicate_reference,
                "details": {
                    "family": "private-first-unrelated-family",
                    "parameter_size": "7B",
                    "quantization_level": "Q4_K_M",
                },
            },
            {
                "model": duplicate_reference,
                "details": {
                    "family": "private-last-unrelated-family",
                    "parameter_size": "70B",
                    "quantization_level": "Q8_0",
                },
            },
        ],
        (target_reference, duplicate_reference),
        reference_selector=lambda reference: reference == target_reference,
        private_values=(
            duplicate_reference,
            "private-first-unrelated-family",
            "private-last-unrelated-family",
        ),
    )


@pytest.mark.asyncio
async def test_ollama_targeted_duplicate_target_prevents_all_show():
    await _assert_duplicate_inventory_rejected_before_show(
        [
            {
                "model": DUPLICATE_MODEL_REFERENCE,
                "details": {"family": "private-first-target-family"},
            },
            {
                "model": DUPLICATE_MODEL_REFERENCE,
                "details": {"family": "private-last-target-family"},
            },
        ],
        (DUPLICATE_MODEL_REFERENCE,),
        reference_selector=lambda reference: (
            reference == DUPLICATE_MODEL_REFERENCE
        ),
        private_values=(
            DUPLICATE_MODEL_REFERENCE,
            "private-first-target-family",
            "private-last-target-family",
        ),
    )


@pytest.mark.asyncio
async def test_ollama_list_then_resolve_only_refetches_selected_detail():
    references = (
        "/private/runtime/model-a:7b",
        "/private/runtime/model-b:14b",
        "/private/runtime/model-c:32b",
    )
    target_reference = references[1]
    tags_count = 0
    detail_requests: list[tuple[int, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tags_count
        if request.url.path == "/api/tags":
            tags_count += 1
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "model": reference,
                            "details": {
                                "family": f"Family{index}",
                                "parameter_size": f"{index}B",
                                "quantization_level": f"Q{index}",
                            },
                        }
                        for index, reference in enumerate(
                            references,
                            start=1,
                        )
                    ]
                },
            )

        reference = json.loads(request.content)["model"]
        detail_requests.append((tags_count, reference))
        if tags_count == 2 and reference != target_reference:
            return httpx.Response(
                503,
                text="private unrelated model failure",
            )
        capabilities = (
            ["embedding", "embedding", "unknown-capability"]
            if reference == target_reference
            else ["completion"]
        )
        return httpx.Response(200, json={"capabilities": capabilities})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        catalog = ModelCatalog(
            (
                OllamaModelDiscoveryRuntime(
                    client,
                    references,
                ),
            )
        )
        listed = await catalog.list_models()
        selected_descriptor = next(
            model
            for model in listed
            if model.display_name == "Family2 2B"
        )
        resolved = await catalog.resolve_model(
            selected_descriptor.model_id
        )

    assert resolved is not None
    assert resolved.descriptor == selected_descriptor
    assert resolved.runtime_reference == target_reference
    assert resolved.descriptor.capabilities == (ModelCapability.EMBEDDINGS,)
    assert detail_requests == [
        (1, references[0]),
        (1, references[1]),
        (1, references[2]),
        (2, target_reference),
    ]
    assert target_reference not in repr(selected_descriptor)


@pytest.mark.asyncio
async def test_ollama_targeted_oversized_show_is_generic_and_closes():
    cap = 256
    references = ("allowed-one", "allowed-two", "allowed-three")
    target_reference = references[1]
    oversized_stream = _CatalogRecordingStream((b"private oversized details",))
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"model": reference, "details": {}}
                        for reference in references
                    ]
                },
            )
        return _catalog_response(
            oversized_stream,
            headers=[(b"Content-Length", str(cap + 1).encode())],
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(ModelRuntimeUnavailableError) as captured:
            await OllamaModelDiscoveryRuntime(
                client,
                references,
                max_response_bytes=cap,
            ).discover_models(
                reference_selector=lambda reference: (
                    reference == target_reference
                )
            )

    assert [request.url.path for request in requests] == [
        "/api/tags",
        "/api/show",
    ]
    assert json.loads(requests[1].content) == {"model": target_reference}
    assert oversized_stream.iteration_count == 0
    assert oversized_stream.closed is True
    assert "private oversized details" not in str(captured.value)


@pytest.mark.asyncio
async def test_ollama_discovery_fails_closed_for_non_allowlisted_models():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion"]})
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "model": LOCAL_MODEL_REFERENCE,
                        "details": {"family": "VerifiedLocal"},
                    },
                    {
                        "model": "gpt-oss:120b-cloud",
                        "details": {"family": "Cloud"},
                    },
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        empty_inventory = await OllamaModelDiscoveryRuntime(
            client
        ).discover_models()
        absent_inventory = await OllamaModelDiscoveryRuntime(
            client,
            ("absent-local-model",),
        ).discover_models()
        allowed_inventory = await OllamaModelDiscoveryRuntime(
            client,
            LOCAL_MODEL_ALLOWLIST,
        ).discover_models()

    assert empty_inventory == ()
    assert absent_inventory == ()
    assert tuple(model.reference for model in allowed_inventory) == (
        LOCAL_MODEL_REFERENCE,
    )
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/api/tags"),
        ("GET", "/api/tags"),
        ("GET", "/api/tags"),
        ("POST", "/api/show"),
    ]
    assert json.loads(requests[3].content) == {
        "model": LOCAL_MODEL_REFERENCE
    }


@pytest.mark.asyncio
async def test_ollama_unsafe_metadata_is_not_promoted_to_public_fields():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            return httpx.Response(
                200,
                json={
                    "capabilities": ["completion"],
                    "template": "/private/template",
                    "parameters": "https://external.invalid/settings",
                    "license": "C:\\private\\license",
                    "model_info": {"secret": "/private/model"},
                },
            )
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "model": "internal-tag",
                        "details": {
                            "family": "/private/models/family",
                            "parameter_size": "https://external.invalid/model",
                            "quantization_level": "C:\\private\\model",
                        },
                    }
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        (model,) = await OllamaModelDiscoveryRuntime(
            client,
            ("internal-tag",),
        ).discover_models()

    assert model.display_name == "Local text model"
    assert model.family is None
    assert model.parameter_class is None
    assert model.quantization is None
    assert model.capabilities == (ModelCapability.TEXT_GENERATION,)


@pytest.mark.parametrize(
    "detail_payload",
    [
        None,
        [],
        {},
        {"capabilities": None},
        {"capabilities": "completion"},
        {"capabilities": ["completion", 1]},
    ],
)
@pytest.mark.asyncio
async def test_ollama_discovery_rejects_malformed_capability_details(
    detail_payload,
):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            return httpx.Response(200, json=detail_payload)
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "model": LOCAL_MODEL_REFERENCE,
                        "details": {"family": "VerifiedLocal"},
                    }
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(ModelRuntimeUnavailableError):
            await OllamaModelDiscoveryRuntime(
                client,
                LOCAL_MODEL_ALLOWLIST,
            ).discover_models()


@pytest.mark.asyncio
async def test_ollama_discovery_rejects_unavailable_capability_details():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            return httpx.Response(503, text="secret runtime detail")
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "model": LOCAL_MODEL_REFERENCE,
                        "details": {"family": "VerifiedLocal"},
                    }
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(
            ModelRuntimeUnavailableError,
            match="inventory is unavailable",
        ) as raised:
            await OllamaModelDiscoveryRuntime(
                client,
                LOCAL_MODEL_ALLOWLIST,
            ).discover_models()

    assert "secret runtime detail" not in str(raised.value)


@pytest.mark.parametrize(
    "max_response_bytes",
    [
        None,
        True,
        False,
        "1",
        1.0,
        0,
        -1,
        MAX_OLLAMA_CATALOG_RESPONSE_BYTES + 1,
    ],
)
def test_ollama_discovery_runtime_rejects_invalid_response_caps(
    max_response_bytes,
):
    with pytest.raises((TypeError, ValueError)):
        OllamaModelDiscoveryRuntime(
            object(),
            max_response_bytes=max_response_bytes,
        )


@pytest.mark.parametrize(
    "max_response_bytes",
    [1, 262_144, MAX_OLLAMA_CATALOG_RESPONSE_BYTES],
)
def test_ollama_discovery_runtime_accepts_bounded_response_caps(
    max_response_bytes,
):
    runtime = OllamaModelDiscoveryRuntime(
        object(),
        max_response_bytes=max_response_bytes,
    )

    assert runtime.max_response_bytes == max_response_bytes


@pytest.mark.parametrize(
    "max_list_models",
    [
        None,
        True,
        False,
        "1",
        1.0,
        0,
        -1,
        MAX_OLLAMA_CATALOG_LIST_MODELS + 1,
    ],
)
def test_ollama_discovery_runtime_rejects_invalid_list_model_caps(
    max_list_models,
):
    with pytest.raises((TypeError, ValueError)):
        OllamaModelDiscoveryRuntime(
            object(),
            max_list_models=max_list_models,
        )


@pytest.mark.parametrize(
    "max_list_models",
    [1, 64, MAX_OLLAMA_CATALOG_LIST_MODELS],
)
def test_ollama_discovery_runtime_accepts_bounded_list_model_caps(
    max_list_models,
):
    runtime = OllamaModelDiscoveryRuntime(
        object(),
        max_list_models=max_list_models,
    )

    assert runtime.max_list_models == max_list_models


@pytest.mark.asyncio
async def test_ollama_tags_accepts_valid_json_exactly_at_cap_and_closes():
    cap = 256
    tags_body = _catalog_json_body_at_size(
        {
            "models": [
                {
                    "model": LOCAL_MODEL_REFERENCE,
                    "details": {"family": "VerifiedLocal"},
                }
            ]
        },
        cap,
    )
    show_body = _catalog_json_body({"capabilities": ["completion"]})
    tags_stream = _CatalogRecordingStream(
        (tags_body[:97], tags_body[97:]),
    )
    show_stream = _CatalogRecordingStream((show_body,))
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/tags":
            return _catalog_response(
                tags_stream,
                headers=[(b"Content-Length", str(cap).encode())],
            )
        return _catalog_response(show_stream)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        (model,) = await OllamaModelDiscoveryRuntime(
            client,
            LOCAL_MODEL_ALLOWLIST,
            max_response_bytes=cap,
        ).discover_models()

    assert model.reference == LOCAL_MODEL_REFERENCE
    assert model.capabilities == (ModelCapability.TEXT_GENERATION,)
    assert tags_stream.iteration_count == 2
    assert tags_stream.closed is True
    assert show_stream.closed is True
    assert all(
        request.headers["Accept-Encoding"] == "identity"
        for request in requests
    )


@pytest.mark.asyncio
async def test_ollama_tags_declared_oversize_does_not_iterate_body():
    cap = 64
    stream = _CatalogRecordingStream((b"private oversized inventory",))

    async def handler(_request: httpx.Request) -> httpx.Response:
        return _catalog_response(
            stream,
            headers=[(b"Content-Length", str(cap + 1).encode())],
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(ModelRuntimeUnavailableError) as captured:
            await OllamaModelDiscoveryRuntime(
                client,
                max_response_bytes=cap,
            ).discover_models()

    assert stream.iteration_count == 0
    assert stream.closed is True
    assert "private oversized inventory" not in str(captured.value)
    assert str(cap + 1) not in str(captured.value)


@pytest.mark.parametrize(
    ("headers", "test_id"),
    [
        ((), "missing"),
        (((b"Content-Length", b"7"),), "understated"),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
@pytest.mark.asyncio
async def test_ollama_tags_actual_overflow_is_cumulative_and_closes(
    headers,
    test_id,
):
    del test_id
    cap = 32
    stream = _CatalogRecordingStream((b"x" * cap, b"y"))

    async def handler(_request: httpx.Request) -> httpx.Response:
        return _catalog_response(stream, headers=headers)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(ModelRuntimeUnavailableError):
            await OllamaModelDiscoveryRuntime(
                client,
                max_response_bytes=cap,
            ).discover_models()

    assert stream.iteration_count == 2
    assert stream.closed is True


@pytest.mark.parametrize(
    "headers",
    [
        [
            (b"Content-Length", b"1"),
            (b"Content-Length", b"2"),
        ],
        [(b"Content-Length", b"1, 1")],
        [(b"Content-Length", b"-1")],
        [(b"Content-Length", b"not-a-length")],
    ],
    ids=["conflicting", "comma-ambiguous", "negative", "malformed"],
)
@pytest.mark.asyncio
async def test_ollama_tags_rejects_unsafe_length_without_reading(headers):
    stream = _CatalogRecordingStream((b"private inventory",))

    async def handler(_request: httpx.Request) -> httpx.Response:
        return _catalog_response(stream, headers=headers)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(ModelRuntimeUnavailableError) as captured:
            await OllamaModelDiscoveryRuntime(client).discover_models()

    assert stream.iteration_count == 0
    assert stream.closed is True
    assert "private inventory" not in str(captured.value)
    assert "Content-Length" not in str(captured.value)


@pytest.mark.asyncio
async def test_ollama_tags_rejects_non_identity_encoding_without_reading():
    stream = _CatalogRecordingStream((b"compressed private inventory",))

    async def handler(_request: httpx.Request) -> httpx.Response:
        return _catalog_response(
            stream,
            headers=[(b"Content-Encoding", b"gzip")],
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(ModelRuntimeUnavailableError) as captured:
            await OllamaModelDiscoveryRuntime(client).discover_models()

    assert stream.iteration_count == 0
    assert stream.closed is True
    assert "compressed private inventory" not in str(captured.value)
    assert "gzip" not in str(captured.value)


@pytest.mark.asyncio
async def test_ollama_tags_non_success_does_not_consume_large_body():
    stream = _CatalogRecordingStream((b"secret runtime status body" * 100,))

    async def handler(_request: httpx.Request) -> httpx.Response:
        return _catalog_response(stream, status_code=599)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(ModelRuntimeUnavailableError) as captured:
            await OllamaModelDiscoveryRuntime(client).discover_models()

    assert stream.iteration_count == 0
    assert stream.closed is True
    assert "599" not in str(captured.value)
    assert "secret runtime status body" not in str(captured.value)


@pytest.mark.asyncio
async def test_ollama_tags_cancellation_closes_stream_and_propagates():
    blocked = asyncio.Event()
    release = asyncio.Event()
    stream = _CatalogRecordingStream(
        (b'{"models":', b"[]}"),
        blocked=blocked,
        release=release,
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return _catalog_response(stream)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        task = asyncio.create_task(
            OllamaModelDiscoveryRuntime(client).discover_models()
        )
        await blocked.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert stream.iteration_count == 1
    assert stream.closed is True


@pytest.mark.asyncio
async def test_ollama_tags_timeout_closes_stream_and_is_generic():
    stream = _CatalogRecordingStream(
        (b"{",),
        failure=httpx.ReadTimeout("secret runtime timeout"),
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return _catalog_response(stream)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
        timeout=5,
    ) as client:
        with pytest.raises(ModelRuntimeUnavailableError) as captured:
            await OllamaModelDiscoveryRuntime(client).discover_models()

    assert stream.closed is True
    assert "secret runtime timeout" not in str(captured.value)


@pytest.mark.asyncio
async def test_ollama_tags_malformed_json_is_generic_and_closes():
    stream = _CatalogRecordingStream((b'{"models":', b"not-json"))

    async def handler(_request: httpx.Request) -> httpx.Response:
        return _catalog_response(stream)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(ModelRuntimeUnavailableError) as captured:
            await OllamaModelDiscoveryRuntime(client).discover_models()

    assert stream.closed is True
    assert "not-json" not in str(captured.value)


@pytest.mark.asyncio
async def test_ollama_catalog_parser_failure_is_generic(monkeypatch):
    stream = _CatalogRecordingStream((b'{"models":[]}',))

    async def handler(_request: httpx.Request) -> httpx.Response:
        return _catalog_response(stream)

    monkeypatch.setattr(
        ollama_runtime_module.json,
        "loads",
        Mock(side_effect=RecursionError("secret nested payload")),
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(ModelRuntimeUnavailableError) as captured:
            await OllamaModelDiscoveryRuntime(client).discover_models()

    assert stream.closed is True
    assert "secret nested payload" not in str(captured.value)


@pytest.mark.parametrize(
    "repeat_last_n",
    [
        True,
        False,
        "64",
        64.0,
        [],
        {},
        float("nan"),
        float("inf"),
        float("-inf"),
        -1,
        -2,
        2049,
    ],
)
@pytest.mark.asyncio
async def test_ollama_generation_rejects_invalid_repeat_last_n_before_http(
    repeat_last_n,
):
    client = Mock(post=AsyncMock())

    with pytest.raises((TypeError, ValueError)):
        await OllamaTextGenerationRuntime(
            client,
            timeout_seconds=37,
            local_model_allowlist=LOCAL_MODEL_ALLOWLIST,
        ).generate_text(
            LOCAL_MODEL_REFERENCE,
            (
                TextGenerationMessage(
                    role=TextGenerationRole.USER,
                    content="prompt",
                ),
            ),
            max_output_tokens=128,
            repeat_last_n=repeat_last_n,
        )

    client.post.assert_not_awaited()


@pytest.mark.parametrize(
    "typical_p",
    [
        True,
        False,
        "0.7",
        [],
        {},
        float("nan"),
        float("inf"),
        float("-inf"),
        -0.01,
        1.01,
        10**1000,
    ],
)
@pytest.mark.asyncio
async def test_ollama_generation_rejects_invalid_typical_p_before_http(
    typical_p,
):
    client = Mock(post=AsyncMock())

    with pytest.raises((TypeError, ValueError)):
        await OllamaTextGenerationRuntime(
            client,
            timeout_seconds=37,
            local_model_allowlist=LOCAL_MODEL_ALLOWLIST,
        ).generate_text(
            LOCAL_MODEL_REFERENCE,
            (
                TextGenerationMessage(
                    role=TextGenerationRole.USER,
                    content="prompt",
                ),
            ),
            max_output_tokens=128,
            typical_p=typical_p,
        )

    client.post.assert_not_awaited()


@pytest.mark.parametrize(
    "presence_penalty",
    [
        True,
        False,
        "1.5",
        [],
        {},
        float("nan"),
        float("inf"),
        float("-inf"),
        -2.01,
        2.01,
        10**1000,
    ],
)
@pytest.mark.asyncio
async def test_ollama_generation_rejects_invalid_presence_penalty_before_http(
    presence_penalty,
):
    client = Mock(post=AsyncMock())

    with pytest.raises((TypeError, ValueError)):
        await OllamaTextGenerationRuntime(
            client,
            timeout_seconds=37,
            local_model_allowlist=LOCAL_MODEL_ALLOWLIST,
        ).generate_text(
            LOCAL_MODEL_REFERENCE,
            (
                TextGenerationMessage(
                    role=TextGenerationRole.USER,
                    content="prompt",
                ),
            ),
            max_output_tokens=128,
            presence_penalty=presence_penalty,
        )

    client.post.assert_not_awaited()


@pytest.mark.parametrize(
    "frequency_penalty",
    [
        True,
        False,
        "1.5",
        [],
        {},
        float("nan"),
        float("inf"),
        float("-inf"),
        -2.01,
        2.01,
        10**1000,
    ],
)
@pytest.mark.asyncio
async def test_ollama_generation_rejects_invalid_frequency_penalty_before_http(
    frequency_penalty,
):
    client = Mock(post=AsyncMock())

    with pytest.raises((TypeError, ValueError)):
        await OllamaTextGenerationRuntime(
            client,
            timeout_seconds=37,
            local_model_allowlist=LOCAL_MODEL_ALLOWLIST,
        ).generate_text(
            LOCAL_MODEL_REFERENCE,
            (
                TextGenerationMessage(
                    role=TextGenerationRole.USER,
                    content="prompt",
                ),
            ),
            max_output_tokens=128,
            frequency_penalty=frequency_penalty,
        )

    client.post.assert_not_awaited()


@pytest.mark.parametrize(
    "stop_sequences",
    [
        "END",
        True,
        1,
        1.0,
        {},
        (),
        [],
        ["a", "b", "c", "d", "e"],
        [None],
        [True],
        [1],
        [1.0],
        [[]],
        [{}],
        [""],
        ["x" * 129],
    ],
)
@pytest.mark.asyncio
async def test_ollama_generation_rejects_invalid_stop_sequences_before_http(
    stop_sequences,
):
    client = Mock(post=AsyncMock())

    with pytest.raises((TypeError, ValueError)):
        await OllamaTextGenerationRuntime(
            client,
            timeout_seconds=37,
            local_model_allowlist=LOCAL_MODEL_ALLOWLIST,
        ).generate_text(
            LOCAL_MODEL_REFERENCE,
            (
                TextGenerationMessage(
                    role=TextGenerationRole.USER,
                    content="prompt",
                ),
            ),
            max_output_tokens=128,
            stop_sequences=stop_sequences,
        )

    client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_ollama_show_accepts_valid_json_exactly_at_cap():
    cap = 256
    tags_body = _catalog_json_body(
        {
            "models": [
                {
                    "model": LOCAL_MODEL_REFERENCE,
                    "details": {
                        "family": "VerifiedLocal",
                        "parameter_size": "14B",
                        "quantization_level": "Q4_K_M",
                    },
                }
            ]
        }
    )
    show_body = _catalog_json_body_at_size(
        {"capabilities": ["completion", "tools", "completion"]},
        cap,
    )
    tags_stream = _CatalogRecordingStream((tags_body,))
    show_stream = _CatalogRecordingStream((show_body[:101], show_body[101:]))

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return _catalog_response(tags_stream)
        return _catalog_response(
            show_stream,
            headers=[(b"Content-Length", str(cap).encode())],
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        (model,) = await OllamaModelDiscoveryRuntime(
            client,
            LOCAL_MODEL_ALLOWLIST,
            max_response_bytes=cap,
        ).discover_models()

    assert model.family == "VerifiedLocal"
    assert model.parameter_class == "14B"
    assert model.quantization == "Q4_K_M"
    assert model.capabilities == (
        ModelCapability.TEXT_GENERATION,
        ModelCapability.TOOL_CALLING,
    )
    assert tags_stream.closed is True
    assert show_stream.iteration_count == 2
    assert show_stream.closed is True


@pytest.mark.asyncio
async def test_ollama_show_discards_large_ignored_fields_below_cap():
    cap = 4_096
    private_value = "/private/runtime/secret/" + "x" * 700
    show_body = _catalog_json_body(
        {
            "capabilities": [
                "tools",
                "completion",
                "vision",
                "embedding",
                "completion",
                "unknown-capability",
            ],
            "template": private_value,
            "parameters": private_value,
            "license": private_value,
            "model_info": {"private.path": private_value},
        }
    )
    assert len(show_body) < cap
    show_stream = _CatalogRecordingStream((show_body,))

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "model": LOCAL_MODEL_REFERENCE,
                            "details": {"family": "VerifiedLocal"},
                        }
                    ]
                },
            )
        return _catalog_response(show_stream)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        (model,) = await OllamaModelDiscoveryRuntime(
            client,
            LOCAL_MODEL_ALLOWLIST,
            max_response_bytes=cap,
        ).discover_models()

    assert model.capabilities == (
        ModelCapability.EMBEDDINGS,
        ModelCapability.TEXT_GENERATION,
        ModelCapability.TOOL_CALLING,
        ModelCapability.VISION_INPUT,
    )
    assert private_value not in repr(model)
    assert show_stream.closed is True


@pytest.mark.parametrize("oversized_index", [0, 1], ids=["first", "later"])
@pytest.mark.asyncio
async def test_ollama_oversized_show_fails_whole_discovery_and_stops(
    oversized_index,
):
    cap = 128
    references = ("allowed-one", "allowed-two", "allowed-three")
    tags_body = _catalog_json_body(
        {
            "models": [
                {"model": reference, "details": {}}
                for reference in references
            ]
        }
    )
    tags_stream = _CatalogRecordingStream((tags_body,))
    successful_streams: list[_CatalogRecordingStream] = []
    oversized_stream = _CatalogRecordingStream((b"private oversized details",))
    requests: list[httpx.Request] = []
    show_index = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal show_index
        requests.append(request)
        if request.url.path == "/api/tags":
            return _catalog_response(tags_stream)
        current_index = show_index
        show_index += 1
        if current_index == oversized_index:
            return _catalog_response(
                oversized_stream,
                headers=[(b"Content-Length", str(cap + 1).encode())],
            )
        stream = _CatalogRecordingStream(
            (_catalog_json_body({"capabilities": ["completion"]}),)
        )
        successful_streams.append(stream)
        return _catalog_response(stream)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(ModelRuntimeUnavailableError) as captured:
            await OllamaModelDiscoveryRuntime(
                client,
                references,
                max_response_bytes=cap,
            ).discover_models()

    assert [request.url.path for request in requests] == [
        "/api/tags",
        *["/api/show"] * (oversized_index + 1),
    ]
    assert oversized_stream.iteration_count == 0
    assert oversized_stream.closed is True
    assert all(stream.closed for stream in successful_streams)
    assert "private oversized details" not in str(captured.value)
    assert all(reference not in str(captured.value) for reference in references)


@pytest.mark.asyncio
async def test_ollama_show_cancellation_closes_current_stream_and_propagates():
    blocked = asyncio.Event()
    release = asyncio.Event()
    show_stream = _CatalogRecordingStream(
        (b'{"capabilities":', b"[]}"),
        blocked=blocked,
        release=release,
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"model": LOCAL_MODEL_REFERENCE, "details": {}}
                    ]
                },
            )
        return _catalog_response(show_stream)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        task = asyncio.create_task(
            OllamaModelDiscoveryRuntime(
                client,
                LOCAL_MODEL_ALLOWLIST,
            ).discover_models(
                reference_selector=lambda reference: reference == LOCAL_MODEL_REFERENCE
            )
        )
        await blocked.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert show_stream.iteration_count == 1
    assert show_stream.closed is True


@pytest.mark.asyncio
async def test_full_list_deadline_during_tags_closes_stream():
    blocked = asyncio.Event()
    never_release = asyncio.Event()
    tags_stream = _CatalogRecordingStream(
        (b'{"models":', b"[]}"),
        blocked=blocked,
        release=never_release,
    )
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _catalog_response(tags_stream)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
        timeout=5,
    ) as client:
        catalog = ModelCatalog(
            (OllamaModelDiscoveryRuntime(client, LOCAL_MODEL_ALLOWLIST),),
            max_list_discovery_seconds=0.01,
        )
        caller = asyncio.create_task(catalog.list_models())
        await blocked.wait()
        with pytest.raises(ModelRuntimeUnavailableError):
            await caller

    assert [request.url.path for request in requests] == ["/api/tags"]
    assert tags_stream.closed is True
    assert catalog._list_models_flight is None


@pytest.mark.parametrize("blocked_show_index", [0, 1], ids=["first", "later"])
@pytest.mark.asyncio
async def test_full_list_deadline_closes_current_show_and_stops_fanout(
    blocked_show_index,
):
    references = (
        "/private/runtime/one:7b",
        "/private/runtime/two:14b",
        "/private/runtime/three:32b",
    )
    blocked = asyncio.Event()
    never_release = asyncio.Event()
    blocked_stream = _CatalogRecordingStream(
        (b'{"capabilities":', b"[]}"),
        blocked=blocked,
        release=never_release,
    )
    successful_streams: list[_CatalogRecordingStream] = []
    requests: list[httpx.Request] = []
    show_index = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal show_index
        requests.append(request)
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"model": reference, "details": {}}
                        for reference in references
                    ]
                },
            )
        current_index = show_index
        show_index += 1
        if current_index == blocked_show_index:
            return _catalog_response(blocked_stream)
        stream = _CatalogRecordingStream(
            (_catalog_json_body({"capabilities": ["completion"]}),)
        )
        successful_streams.append(stream)
        return _catalog_response(stream)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
        timeout=5,
    ) as client:
        catalog = ModelCatalog(
            (OllamaModelDiscoveryRuntime(client, references),),
            max_list_discovery_seconds=0.01,
        )
        caller = asyncio.create_task(catalog.list_models())
        await blocked.wait()
        with pytest.raises(ModelRuntimeUnavailableError):
            await caller

    assert [request.url.path for request in requests] == [
        "/api/tags",
        *["/api/show"] * (blocked_show_index + 1),
    ]
    assert blocked_stream.closed is True
    assert all(stream.closed for stream in successful_streams)
    assert catalog._list_models_flight is None


@pytest.mark.asyncio
async def test_ollama_show_timeout_closes_current_stream_and_is_generic():
    show_stream = _CatalogRecordingStream(
        (b"{",),
        failure=httpx.ReadTimeout("secret detail timeout"),
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"model": LOCAL_MODEL_REFERENCE, "details": {}}
                    ]
                },
            )
        return _catalog_response(show_stream)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
        timeout=5,
    ) as client:
        with pytest.raises(ModelRuntimeUnavailableError) as captured:
            await OllamaModelDiscoveryRuntime(
                client,
                LOCAL_MODEL_ALLOWLIST,
            ).discover_models(
                reference_selector=lambda reference: reference == LOCAL_MODEL_REFERENCE
            )

    assert show_stream.closed is True
    assert "secret detail timeout" not in str(captured.value)


@pytest.mark.asyncio
async def test_ollama_show_malformed_json_is_generic_and_closes():
    show_stream = _CatalogRecordingStream(
        (b'{"capabilities":', b"private-invalid-json"),
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"model": LOCAL_MODEL_REFERENCE, "details": {}}
                    ]
                },
            )
        return _catalog_response(show_stream)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(ModelRuntimeUnavailableError) as captured:
            await OllamaModelDiscoveryRuntime(
                client,
                LOCAL_MODEL_ALLOWLIST,
            ).discover_models(
                reference_selector=lambda reference: reference == LOCAL_MODEL_REFERENCE
            )

    assert show_stream.closed is True
    assert "private-invalid-json" not in str(captured.value)


@pytest.mark.asyncio
async def test_ollama_catalog_opaque_public_id_is_stable_for_same_reference():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"model": LOCAL_MODEL_REFERENCE, "details": {}}
                    ]
                },
            )
        return httpx.Response(
            200,
            json={"capabilities": ["completion"]},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        catalog = ModelCatalog(
            (
                OllamaModelDiscoveryRuntime(
                    client,
                    LOCAL_MODEL_ALLOWLIST,
                ),
            )
        )
        first = await catalog.list_models()
        second = await catalog.list_models()
        resolved = await catalog.resolve_model(first[0].model_id)

    assert first[0].model_id == second[0].model_id
    assert resolved is not None
    assert resolved.descriptor == first[0]
    assert resolved.runtime_reference == LOCAL_MODEL_REFERENCE
    assert first[0].model_id == "ollama-local:a92a93894cc39a91a63c2b3b"
    assert first[0].model_id.startswith("ollama-local:")
    assert LOCAL_MODEL_REFERENCE not in repr(first)


@pytest.mark.parametrize(
    "payload",
    [None, {}, {"models": None}, {"models": [None]}, {"models": [{}]}],
)
@pytest.mark.asyncio
async def test_ollama_malformed_inventory_is_generic_runtime_unavailable(payload):
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(ModelRuntimeUnavailableError):
            await OllamaModelDiscoveryRuntime(client).discover_models()


@pytest.mark.parametrize("failure", ["timeout", "status"])
@pytest.mark.asyncio
async def test_ollama_transport_failures_are_generic_runtime_unavailable(failure):
    async def handler(request: httpx.Request) -> httpx.Response:
        if failure == "timeout":
            raise httpx.ConnectTimeout("secret runtime URL", request=request)
        return httpx.Response(503, text="secret runtime persistence detail")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(ModelRuntimeUnavailableError) as captured:
            await OllamaModelDiscoveryRuntime(client).discover_models()

    assert "secret" not in str(captured.value)


def test_ollama_client_disables_proxies_and_redirects(monkeypatch):
    client = object()
    constructor = Mock(return_value=client)
    monkeypatch.setattr(ollama_client_module.httpx, "AsyncClient", constructor)
    settings = Settings(
        _env_file=None,
        OLLAMA_BASE_URL="http://127.0.0.1:11434",
        OLLAMA_TIMEOUT_SECONDS=7,
    )

    assert create_ollama_client(settings) is client
    constructor.assert_called_once_with(
        base_url="http://127.0.0.1:11434",
        timeout=7,
        follow_redirects=False,
        trust_env=False,
    )


@pytest.mark.asyncio
async def test_ollama_readiness_is_status_only_and_closes_success_stream():
    stream = _CatalogRecordingStream((b"unused private inventory" * 100,))
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _catalog_response(stream)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
        timeout=7,
    ) as client:
        await check_ollama(client)

    assert stream.iteration_count == 0
    assert stream.closed is True
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert request.url.path == "/api/tags"
    assert request.headers["Accept-Encoding"] == "identity"
    assert request.extensions["timeout"] == {
        "connect": 7,
        "read": 7,
        "write": 7,
        "pool": 7,
    }


@pytest.mark.asyncio
async def test_ollama_readiness_non_success_does_not_read_and_closes():
    stream = _CatalogRecordingStream((b"private readiness error" * 100,))

    async def handler(_request: httpx.Request) -> httpx.Response:
        return _catalog_response(stream, status_code=503)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(httpx.HTTPStatusError) as captured:
            await check_ollama(client)

    assert stream.iteration_count == 0
    assert stream.closed is True
    assert "private readiness error" not in str(captured.value)


@pytest.mark.asyncio
async def test_ollama_readiness_cancellation_closes_stream_and_propagates():
    close_started = asyncio.Event()
    close_release = asyncio.Event()
    stream = _CatalogRecordingStream(
        (b"unused inventory",),
        close_started=close_started,
        close_release=close_release,
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return _catalog_response(stream)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        task = asyncio.create_task(check_ollama(client))
        await close_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert stream.iteration_count == 0
    assert stream.closed is True


@pytest.mark.asyncio
async def test_ollama_readiness_preserves_client_timeout_behavior():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise httpx.ReadTimeout("secret readiness timeout", request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
        timeout=5,
    ) as client:
        with pytest.raises(httpx.ReadTimeout) as captured:
            await check_ollama(client)

    assert "secret readiness timeout" in str(captured.value)
    assert requests[0].extensions["timeout"] == {
        "connect": 5,
        "read": 5,
        "write": 5,
        "pool": 5,
    }


@pytest.mark.asyncio
async def test_ollama_generation_is_non_streaming_bounded_and_preserves_content():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "done": True,
                "message": {
                    "role": "assistant",
                    "content": "  exact generated content  ",
                },
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
        trust_env=False,
        follow_redirects=False,
    ) as client:
        result = await OllamaTextGenerationRuntime(
            client,
            timeout_seconds=37,
            local_model_allowlist=LOCAL_MODEL_ALLOWLIST,
        ).generate_text(
            LOCAL_MODEL_REFERENCE,
            (
                TextGenerationMessage(
                    role=TextGenerationRole.SYSTEM,
                    content="system",
                ),
                TextGenerationMessage(
                    role=TextGenerationRole.USER,
                    content="  exact prompt  ",
                ),
            ),
            max_output_tokens=1024,
        )

    assert result.content == "  exact generated content  "
    assert len(requests) == 1
    request = requests[0]
    assert (request.method, request.url.path) == ("POST", "/api/chat")
    assert request.headers["Accept-Encoding"] == "identity"
    assert request.extensions["timeout"] == {
        "connect": 37.0,
        "read": 37.0,
        "write": 37.0,
        "pool": 37.0,
    }
    assert request.read()
    import json

    assert json.loads(request.content) == {
        "model": LOCAL_MODEL_REFERENCE,
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "  exact prompt  "},
        ],
        "stream": False,
        "options": {"num_predict": 1024},
    }


@pytest.mark.parametrize("temperature", [0, 1, 2, 0.5, 2.0])
@pytest.mark.asyncio
async def test_ollama_generation_forwards_exact_valid_temperature(temperature):
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "done": True,
                "message": {
                    "role": "assistant",
                    "content": "answer",
                },
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
        trust_env=False,
        follow_redirects=False,
    ) as client:
        result = await OllamaTextGenerationRuntime(
            client,
            timeout_seconds=37,
            local_model_allowlist=LOCAL_MODEL_ALLOWLIST,
        ).generate_text(
            LOCAL_MODEL_REFERENCE,
            (
                TextGenerationMessage(
                    role=TextGenerationRole.USER,
                    content="prompt",
                ),
            ),
            max_output_tokens=128,
            temperature=temperature,
        )

    assert result.content == "answer"
    assert len(requests) == 1
    import json

    assert json.loads(requests[0].content) == {
        "model": LOCAL_MODEL_REFERENCE,
        "messages": [{"role": "user", "content": "prompt"}],
        "stream": False,
        "options": {
            "num_predict": 128,
            "temperature": temperature,
        },
    }


@pytest.mark.parametrize("seed", [0, 42, 2_147_483_647])
@pytest.mark.asyncio
async def test_ollama_generation_forwards_exact_valid_seed(seed):
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "done": True,
                "message": {
                    "role": "assistant",
                    "content": "answer",
                },
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
        trust_env=False,
        follow_redirects=False,
    ) as client:
        result = await OllamaTextGenerationRuntime(
            client,
            timeout_seconds=37,
            local_model_allowlist=LOCAL_MODEL_ALLOWLIST,
        ).generate_text(
            LOCAL_MODEL_REFERENCE,
            (
                TextGenerationMessage(
                    role=TextGenerationRole.USER,
                    content="prompt",
                ),
            ),
            max_output_tokens=128,
            seed=seed,
        )

    assert result.content == "answer"
    assert len(requests) == 1
    import json

    assert json.loads(requests[0].content) == {
        "model": LOCAL_MODEL_REFERENCE,
        "messages": [{"role": "user", "content": "prompt"}],
        "stream": False,
        "options": {
            "num_predict": 128,
            "seed": seed,
        },
    }


@pytest.mark.parametrize("top_p", [0, 1, 0.5, 0.9, 1.0])
@pytest.mark.asyncio
async def test_ollama_generation_forwards_exact_valid_top_p(top_p):
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "done": True,
                "message": {
                    "role": "assistant",
                    "content": "answer",
                },
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
        trust_env=False,
        follow_redirects=False,
    ) as client:
        result = await OllamaTextGenerationRuntime(
            client,
            timeout_seconds=37,
            local_model_allowlist=LOCAL_MODEL_ALLOWLIST,
        ).generate_text(
            LOCAL_MODEL_REFERENCE,
            (
                TextGenerationMessage(
                    role=TextGenerationRole.USER,
                    content="prompt",
                ),
            ),
            max_output_tokens=128,
            top_p=top_p,
        )

    assert result.content == "answer"
    assert len(requests) == 1
    import json

    assert json.loads(requests[0].content) == {
        "model": LOCAL_MODEL_REFERENCE,
        "messages": [{"role": "user", "content": "prompt"}],
        "stream": False,
        "options": {
            "num_predict": 128,
            "top_p": top_p,
        },
    }


@pytest.mark.parametrize("top_k", [1, 40, 100])
@pytest.mark.asyncio
async def test_ollama_generation_forwards_exact_valid_top_k(top_k):
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "done": True,
                "message": {
                    "role": "assistant",
                    "content": "answer",
                },
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
        trust_env=False,
        follow_redirects=False,
    ) as client:
        result = await OllamaTextGenerationRuntime(
            client,
            timeout_seconds=37,
            local_model_allowlist=LOCAL_MODEL_ALLOWLIST,
        ).generate_text(
            LOCAL_MODEL_REFERENCE,
            (
                TextGenerationMessage(
                    role=TextGenerationRole.USER,
                    content="prompt",
                ),
            ),
            max_output_tokens=128,
            top_k=top_k,
        )

    assert result.content == "answer"
    assert len(requests) == 1
    import json

    assert json.loads(requests[0].content) == {
        "model": LOCAL_MODEL_REFERENCE,
        "messages": [{"role": "user", "content": "prompt"}],
        "stream": False,
        "options": {
            "num_predict": 128,
            "top_k": top_k,
        },
    }


@pytest.mark.parametrize("min_p", [0, 1, 0.05, 0.5, 1.0])
@pytest.mark.asyncio
async def test_ollama_generation_forwards_exact_valid_min_p(min_p):
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "done": True,
                "message": {
                    "role": "assistant",
                    "content": "answer",
                },
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
        trust_env=False,
        follow_redirects=False,
    ) as client:
        result = await OllamaTextGenerationRuntime(
            client,
            timeout_seconds=37,
            local_model_allowlist=LOCAL_MODEL_ALLOWLIST,
        ).generate_text(
            LOCAL_MODEL_REFERENCE,
            (
                TextGenerationMessage(
                    role=TextGenerationRole.USER,
                    content="prompt",
                ),
            ),
            max_output_tokens=128,
            min_p=min_p,
        )

    assert result.content == "answer"
    assert len(requests) == 1
    import json

    assert json.loads(requests[0].content) == {
        "model": LOCAL_MODEL_REFERENCE,
        "messages": [{"role": "user", "content": "prompt"}],
        "stream": False,
        "options": {
            "num_predict": 128,
            "min_p": min_p,
        },
    }


@pytest.mark.parametrize("repeat_penalty", [0.5, 0.9, 1, 1.1, 1.5, 2.0])
@pytest.mark.asyncio
async def test_ollama_generation_forwards_exact_valid_repeat_penalty(
    repeat_penalty,
):
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "done": True,
                "message": {
                    "role": "assistant",
                    "content": "answer",
                },
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
        trust_env=False,
        follow_redirects=False,
    ) as client:
        result = await OllamaTextGenerationRuntime(
            client,
            timeout_seconds=37,
            local_model_allowlist=LOCAL_MODEL_ALLOWLIST,
        ).generate_text(
            LOCAL_MODEL_REFERENCE,
            (
                TextGenerationMessage(
                    role=TextGenerationRole.USER,
                    content="prompt",
                ),
            ),
            max_output_tokens=128,
            repeat_penalty=repeat_penalty,
        )

    assert result.content == "answer"
    assert len(requests) == 1
    import json

    assert json.loads(requests[0].content) == {
        "model": LOCAL_MODEL_REFERENCE,
        "messages": [{"role": "user", "content": "prompt"}],
        "stream": False,
        "options": {
            "num_predict": 128,
            "repeat_penalty": repeat_penalty,
        },
    }


@pytest.mark.parametrize("repeat_last_n", [0, 1, 64, 2048])
@pytest.mark.asyncio
async def test_ollama_generation_forwards_exact_valid_repeat_last_n(
    repeat_last_n,
):
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "done": True,
                "message": {
                    "role": "assistant",
                    "content": "answer",
                },
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
        trust_env=False,
        follow_redirects=False,
    ) as client:
        result = await OllamaTextGenerationRuntime(
            client,
            timeout_seconds=37,
            local_model_allowlist=LOCAL_MODEL_ALLOWLIST,
        ).generate_text(
            LOCAL_MODEL_REFERENCE,
            (
                TextGenerationMessage(
                    role=TextGenerationRole.USER,
                    content="prompt",
                ),
            ),
            max_output_tokens=128,
            repeat_last_n=repeat_last_n,
        )

    assert result.content == "answer"
    assert len(requests) == 1
    import json

    assert json.loads(requests[0].content) == {
        "model": LOCAL_MODEL_REFERENCE,
        "messages": [{"role": "user", "content": "prompt"}],
        "stream": False,
        "options": {
            "num_predict": 128,
            "repeat_last_n": repeat_last_n,
        },
    }


@pytest.mark.parametrize("typical_p", [0, 1, 0.05, 0.7, 1.0])
@pytest.mark.asyncio
async def test_ollama_generation_forwards_exact_valid_typical_p(typical_p):
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "done": True,
                "message": {
                    "role": "assistant",
                    "content": "answer",
                },
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
        trust_env=False,
        follow_redirects=False,
    ) as client:
        result = await OllamaTextGenerationRuntime(
            client,
            timeout_seconds=37,
            local_model_allowlist=LOCAL_MODEL_ALLOWLIST,
        ).generate_text(
            LOCAL_MODEL_REFERENCE,
            (
                TextGenerationMessage(
                    role=TextGenerationRole.USER,
                    content="prompt",
                ),
            ),
            max_output_tokens=128,
            typical_p=typical_p,
        )

    assert result.content == "answer"
    assert len(requests) == 1
    import json

    assert json.loads(requests[0].content) == {
        "model": LOCAL_MODEL_REFERENCE,
        "messages": [{"role": "user", "content": "prompt"}],
        "stream": False,
        "options": {
            "num_predict": 128,
            "typical_p": typical_p,
        },
    }


@pytest.mark.parametrize(
    "presence_penalty",
    [-2, -1, 0, 0.5, 1, 1.5, 2.0],
)
@pytest.mark.asyncio
async def test_ollama_generation_forwards_exact_valid_presence_penalty(
    presence_penalty,
):
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "done": True,
                "message": {
                    "role": "assistant",
                    "content": "answer",
                },
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
        trust_env=False,
        follow_redirects=False,
    ) as client:
        result = await OllamaTextGenerationRuntime(
            client,
            timeout_seconds=37,
            local_model_allowlist=LOCAL_MODEL_ALLOWLIST,
        ).generate_text(
            LOCAL_MODEL_REFERENCE,
            (
                TextGenerationMessage(
                    role=TextGenerationRole.USER,
                    content="prompt",
                ),
            ),
            max_output_tokens=128,
            presence_penalty=presence_penalty,
        )

    assert result.content == "answer"
    assert len(requests) == 1
    import json

    assert json.loads(requests[0].content) == {
        "model": LOCAL_MODEL_REFERENCE,
        "messages": [{"role": "user", "content": "prompt"}],
        "stream": False,
        "options": {
            "num_predict": 128,
            "presence_penalty": presence_penalty,
        },
    }


@pytest.mark.parametrize(
    "frequency_penalty",
    [-2, -1, 0, 0.5, 1, 1.5, 2.0],
)
@pytest.mark.asyncio
async def test_ollama_generation_forwards_exact_valid_frequency_penalty(
    frequency_penalty,
):
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "done": True,
                "message": {
                    "role": "assistant",
                    "content": "answer",
                },
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
        trust_env=False,
        follow_redirects=False,
    ) as client:
        result = await OllamaTextGenerationRuntime(
            client,
            timeout_seconds=37,
            local_model_allowlist=LOCAL_MODEL_ALLOWLIST,
        ).generate_text(
            LOCAL_MODEL_REFERENCE,
            (
                TextGenerationMessage(
                    role=TextGenerationRole.USER,
                    content="prompt",
                ),
            ),
            max_output_tokens=128,
            frequency_penalty=frequency_penalty,
        )

    assert result.content == "answer"
    assert len(requests) == 1
    import json

    assert json.loads(requests[0].content) == {
        "model": LOCAL_MODEL_REFERENCE,
        "messages": [{"role": "user", "content": "prompt"}],
        "stream": False,
        "options": {
            "num_predict": 128,
            "frequency_penalty": frequency_penalty,
        },
    }


@pytest.mark.parametrize(
    "stop_sequences",
    [
        ["END"],
        ["\n", "\t", "\n", "\u0000"],
        ["界" * 128],
    ],
)
@pytest.mark.asyncio
async def test_ollama_generation_forwards_exact_valid_stop_sequences(
    stop_sequences,
):
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "done": True,
                "message": {
                    "role": "assistant",
                    "content": "answer",
                },
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
        trust_env=False,
        follow_redirects=False,
    ) as client:
        result = await OllamaTextGenerationRuntime(
            client,
            timeout_seconds=37,
            local_model_allowlist=LOCAL_MODEL_ALLOWLIST,
        ).generate_text(
            LOCAL_MODEL_REFERENCE,
            (
                TextGenerationMessage(
                    role=TextGenerationRole.USER,
                    content="prompt",
                ),
            ),
            max_output_tokens=128,
            stop_sequences=stop_sequences,
        )

    assert result.content == "answer"
    assert len(requests) == 1
    import json

    assert json.loads(requests[0].content) == {
        "model": LOCAL_MODEL_REFERENCE,
        "messages": [{"role": "user", "content": "prompt"}],
        "stream": False,
        "options": {
            "num_predict": 128,
            "stop": stop_sequences,
        },
    }


@pytest.mark.asyncio
async def test_ollama_generation_combines_all_bounded_options():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "done": True,
                "message": {
                    "role": "assistant",
                    "content": "answer",
                },
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
        trust_env=False,
        follow_redirects=False,
    ) as client:
        result = await OllamaTextGenerationRuntime(
            client,
            timeout_seconds=37,
            local_model_allowlist=LOCAL_MODEL_ALLOWLIST,
        ).generate_text(
            LOCAL_MODEL_REFERENCE,
            (
                TextGenerationMessage(
                    role=TextGenerationRole.USER,
                    content="prompt",
                ),
            ),
            max_output_tokens=128,
            temperature=0.5,
            seed=42,
            top_p=0.9,
            top_k=40,
            min_p=0.05,
            repeat_penalty=1.1,
            repeat_last_n=64,
            typical_p=0.7,
            presence_penalty=1.5,
            frequency_penalty=0.75,
            stop_sequences=["\n", "END", "\n"],
        )

    assert result.content == "answer"
    assert len(requests) == 1
    import json

    assert json.loads(requests[0].content) == {
        "model": LOCAL_MODEL_REFERENCE,
        "messages": [{"role": "user", "content": "prompt"}],
        "stream": False,
        "options": {
            "num_predict": 128,
            "temperature": 0.5,
            "seed": 42,
            "top_p": 0.9,
            "top_k": 40,
            "min_p": 0.05,
            "repeat_penalty": 1.1,
            "repeat_last_n": 64,
            "typical_p": 0.7,
            "presence_penalty": 1.5,
            "frequency_penalty": 0.75,
            "stop": ["\n", "END", "\n"],
        },
    }


@pytest.mark.parametrize(
    "temperature",
    [
        True,
        False,
        "0.5",
        [],
        {},
        float("nan"),
        float("inf"),
        float("-inf"),
        -0.01,
        2.01,
        10**1000,
    ],
)
@pytest.mark.asyncio
async def test_ollama_generation_rejects_invalid_temperature_before_http(
    temperature,
):
    client = Mock(post=AsyncMock())

    with pytest.raises((TypeError, ValueError)):
        await OllamaTextGenerationRuntime(
            client,
            timeout_seconds=37,
            local_model_allowlist=LOCAL_MODEL_ALLOWLIST,
        ).generate_text(
            LOCAL_MODEL_REFERENCE,
            (
                TextGenerationMessage(
                    role=TextGenerationRole.USER,
                    content="prompt",
                ),
            ),
            max_output_tokens=128,
            temperature=temperature,
        )

    client.post.assert_not_awaited()


@pytest.mark.parametrize(
    "seed",
    [
        True,
        False,
        "42",
        42.0,
        [],
        {},
        float("nan"),
        float("inf"),
        float("-inf"),
        -1,
        2_147_483_648,
    ],
)
@pytest.mark.asyncio
async def test_ollama_generation_rejects_invalid_seed_before_http(seed):
    client = Mock(post=AsyncMock())

    with pytest.raises((TypeError, ValueError)):
        await OllamaTextGenerationRuntime(
            client,
            timeout_seconds=37,
            local_model_allowlist=LOCAL_MODEL_ALLOWLIST,
        ).generate_text(
            LOCAL_MODEL_REFERENCE,
            (
                TextGenerationMessage(
                    role=TextGenerationRole.USER,
                    content="prompt",
                ),
            ),
            max_output_tokens=128,
            seed=seed,
        )

    client.post.assert_not_awaited()


@pytest.mark.parametrize(
    "top_p",
    [
        True,
        False,
        "0.9",
        [],
        {},
        float("nan"),
        float("inf"),
        float("-inf"),
        -0.01,
        1.01,
        10**1000,
    ],
)
@pytest.mark.asyncio
async def test_ollama_generation_rejects_invalid_top_p_before_http(top_p):
    client = Mock(post=AsyncMock())

    with pytest.raises((TypeError, ValueError)):
        await OllamaTextGenerationRuntime(
            client,
            timeout_seconds=37,
            local_model_allowlist=LOCAL_MODEL_ALLOWLIST,
        ).generate_text(
            LOCAL_MODEL_REFERENCE,
            (
                TextGenerationMessage(
                    role=TextGenerationRole.USER,
                    content="prompt",
                ),
            ),
            max_output_tokens=128,
            top_p=top_p,
        )

    client.post.assert_not_awaited()


@pytest.mark.parametrize(
    "top_k",
    [
        True,
        False,
        "40",
        40.0,
        [],
        {},
        float("nan"),
        float("inf"),
        float("-inf"),
        0,
        -1,
        101,
    ],
)
@pytest.mark.asyncio
async def test_ollama_generation_rejects_invalid_top_k_before_http(top_k):
    client = Mock(post=AsyncMock())

    with pytest.raises((TypeError, ValueError)):
        await OllamaTextGenerationRuntime(
            client,
            timeout_seconds=37,
            local_model_allowlist=LOCAL_MODEL_ALLOWLIST,
        ).generate_text(
            LOCAL_MODEL_REFERENCE,
            (
                TextGenerationMessage(
                    role=TextGenerationRole.USER,
                    content="prompt",
                ),
            ),
            max_output_tokens=128,
            top_k=top_k,
        )

    client.post.assert_not_awaited()


@pytest.mark.parametrize(
    "min_p",
    [
        True,
        False,
        "0.05",
        [],
        {},
        float("nan"),
        float("inf"),
        float("-inf"),
        -0.01,
        1.01,
        10**1000,
    ],
)
@pytest.mark.asyncio
async def test_ollama_generation_rejects_invalid_min_p_before_http(min_p):
    client = Mock(post=AsyncMock())

    with pytest.raises((TypeError, ValueError)):
        await OllamaTextGenerationRuntime(
            client,
            timeout_seconds=37,
            local_model_allowlist=LOCAL_MODEL_ALLOWLIST,
        ).generate_text(
            LOCAL_MODEL_REFERENCE,
            (
                TextGenerationMessage(
                    role=TextGenerationRole.USER,
                    content="prompt",
                ),
            ),
            max_output_tokens=128,
            min_p=min_p,
        )

    client.post.assert_not_awaited()


@pytest.mark.parametrize(
    "repeat_penalty",
    [
        True,
        False,
        "1.1",
        [],
        {},
        float("nan"),
        float("inf"),
        float("-inf"),
        -0.01,
        0.49,
        2.01,
        10**1000,
    ],
)
@pytest.mark.asyncio
async def test_ollama_generation_rejects_invalid_repeat_penalty_before_http(
    repeat_penalty,
):
    client = Mock(post=AsyncMock())

    with pytest.raises((TypeError, ValueError)):
        await OllamaTextGenerationRuntime(
            client,
            timeout_seconds=37,
            local_model_allowlist=LOCAL_MODEL_ALLOWLIST,
        ).generate_text(
            LOCAL_MODEL_REFERENCE,
            (
                TextGenerationMessage(
                    role=TextGenerationRole.USER,
                    content="prompt",
                ),
            ),
            max_output_tokens=128,
            repeat_penalty=repeat_penalty,
        )

    client.post.assert_not_awaited()


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"done": True, "message": None},
        {
            "message": {
                "role": "assistant",
                "content": "apparently complete",
            },
        },
        *[
            {
                "done": done,
                "message": {
                    "role": "assistant",
                    "content": "apparently complete",
                },
            }
            for done in (None, False, 1, "true")
        ],
        {"done": True, "message": {}},
        {
            "done": True,
            "message": {"role": "user", "content": "answer"},
        },
        {"done": True, "message": {"role": "assistant"}},
        {
            "done": True,
            "message": {"role": "assistant", "content": ""},
        },
        {
            "done": True,
            "message": {"role": "assistant", "content": "   "},
        },
    ],
)
@pytest.mark.asyncio
async def test_ollama_malformed_generation_is_generic_unavailable(payload):
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(TextGenerationRuntimeUnavailableError) as captured:
            await OllamaTextGenerationRuntime(
                client,
                10,
                ("secret-model-tag",),
            ).generate_text(
                "secret-model-tag",
                (
                    TextGenerationMessage(
                        role=TextGenerationRole.USER,
                        content="secret prompt",
                    ),
                ),
                max_output_tokens=1024,
            )

    assert "secret" not in str(captured.value)


@pytest.mark.asyncio
async def test_ollama_generation_rechecks_local_allowlist_before_http():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "done": True,
                "message": {"role": "assistant", "content": "unsafe"},
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(TextGenerationRuntimeUnsupportedError):
            await OllamaTextGenerationRuntime(
                client,
                10,
                LOCAL_MODEL_ALLOWLIST,
            ).generate_text(
                "gpt-oss:120b-cloud",
                (
                    TextGenerationMessage(
                        role=TextGenerationRole.USER,
                        content="private conversation",
                    ),
                ),
                max_output_tokens=1024,
            )

    assert requests == []


@pytest.mark.parametrize(
    "timeout_seconds",
    [float("inf"), float("-inf"), float("nan")],
)
def test_ollama_generation_timeout_must_be_finite(timeout_seconds):
    with pytest.raises(ValueError, match="positive and finite"):
        OllamaTextGenerationRuntime(
            object(), timeout_seconds, LOCAL_MODEL_ALLOWLIST
        )


@pytest.mark.parametrize("failure", ["timeout", "status"])
@pytest.mark.asyncio
async def test_ollama_generation_transport_failures_are_generic(failure):
    async def handler(request: httpx.Request) -> httpx.Response:
        if failure == "timeout":
            raise httpx.ReadTimeout("secret runtime detail", request=request)
        return httpx.Response(500, text="secret runtime body")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(TextGenerationRuntimeUnavailableError) as captured:
            await OllamaTextGenerationRuntime(
                client,
                10,
                ("secret-model-tag",),
            ).generate_text(
                "secret-model-tag",
                (
                    TextGenerationMessage(
                        role=TextGenerationRole.USER,
                        content="prompt",
                    ),
                ),
                max_output_tokens=1024,
            )

    assert "secret" not in str(captured.value)


class _RecordingAsyncByteStream(httpx.AsyncByteStream):
    def __init__(
        self,
        chunks,
        *,
        blocked: asyncio.Event | None = None,
        release: asyncio.Event | None = None,
        failure: Exception | None = None,
    ):
        self.chunks = tuple(chunks)
        self.blocked = blocked
        self.release = release
        self.failure = failure
        self.iteration_count = 0
        self.closed = False

    async def __aiter__(self):
        for index, chunk in enumerate(self.chunks):
            self.iteration_count += 1
            yield chunk
            if index == 0 and self.blocked is not None:
                self.blocked.set()
                if self.release is None:
                    raise AssertionError("blocked stream requires a release event")
                await self.release.wait()
        if self.failure is not None:
            raise self.failure

    async def aclose(self):
        self.closed = True


def _track_generation_response_buffers(monkeypatch):
    buffers = []

    class TrackingResponseBuffer(bytearray):
        def __init__(self):
            super().__init__()
            self.clear_count = 0

        def clear(self):
            self.clear_count += 1
            super().clear()

    def make_response_buffer():
        buffer = TrackingResponseBuffer()
        buffers.append(buffer)
        return buffer

    monkeypatch.setattr(
        ollama_runtime_module, "bytearray", make_response_buffer, raising=False
    )
    return buffers


def _generation_response_body(content: str) -> bytes:
    return json.dumps(
        {
            "done": True,
            "message": {
                "role": "assistant",
                "content": content,
            },
        },
        separators=(",", ":"),
    ).encode()


def _exact_generation_response_body(size: int) -> bytes:
    empty = _generation_response_body("")
    content_size = size - len(empty)
    if content_size < 1:
        raise ValueError("test response size cannot hold nonblank content")
    body = _generation_response_body("x" * content_size)
    assert len(body) == size
    return body


def _generation_transport(
    stream: _RecordingAsyncByteStream,
    *,
    status_code: int = 200,
    headers=(),
):
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            status_code,
            headers=list(headers),
            stream=stream,
        )

    return httpx.MockTransport(handler), requests


async def _generate_with_runtime(
    runtime: OllamaTextGenerationRuntime,
    *,
    max_output_tokens: int = 128,
):
    return await runtime.generate_text(
        LOCAL_MODEL_REFERENCE,
        (
            TextGenerationMessage(
                role=TextGenerationRole.USER,
                content="prompt",
            ),
        ),
        max_output_tokens=max_output_tokens,
    )


@pytest.mark.parametrize(
    "max_response_bytes",
    [
        None,
        True,
        False,
        "1",
        1.0,
        0,
        -1,
        MAX_OLLAMA_GENERATION_RESPONSE_BYTES + 1,
    ],
)
def test_ollama_generation_runtime_rejects_invalid_response_caps(
    max_response_bytes,
):
    with pytest.raises((TypeError, ValueError)):
        OllamaTextGenerationRuntime(
            object(),
            10,
            LOCAL_MODEL_ALLOWLIST,
            max_response_bytes=max_response_bytes,
        )


@pytest.mark.parametrize(
    "max_response_bytes",
    [1, 262_144, MAX_OLLAMA_GENERATION_RESPONSE_BYTES],
)
def test_ollama_generation_runtime_accepts_bounded_response_caps(
    max_response_bytes,
):
    runtime = OllamaTextGenerationRuntime(
        object(),
        10,
        LOCAL_MODEL_ALLOWLIST,
        max_response_bytes=max_response_bytes,
    )

    assert runtime.max_response_bytes == max_response_bytes


@pytest.mark.asyncio
async def test_ollama_generation_accepts_valid_response_exactly_at_cap(monkeypatch):
    cap = 128
    response_buffers = _track_generation_response_buffers(monkeypatch)
    body = _exact_generation_response_body(cap)
    stream = _RecordingAsyncByteStream((body[:47], body[47:]))
    transport, requests = _generation_transport(
        stream,
        headers=((b"content-length", str(cap).encode()),),
    )

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:11434",
    ) as client:
        result = await _generate_with_runtime(
            OllamaTextGenerationRuntime(
                client,
                10,
                LOCAL_MODEL_ALLOWLIST,
                max_response_bytes=cap,
            )
        )

    assert result.content == "x" * (
        cap - len(_generation_response_body(""))
    )
    assert len(requests) == 1
    assert stream.iteration_count == 2
    assert stream.closed is True
    assert len(response_buffers) == 1
    assert response_buffers[0].clear_count == 1
    assert len(response_buffers[0]) == 0


@pytest.mark.asyncio
async def test_ollama_generation_absent_length_stops_at_cap_plus_one_and_is_safe(
    caplog,
):
    cap = 65_537
    response_fragment = b"private-response-fragment credential-never-consumed"
    stream = _RecordingAsyncByteStream(
        (
            b"x" * cap,
            b"y",
            response_fragment,
        )
    )
    transport, requests = _generation_transport(stream)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(TextGenerationRuntimeUnavailableError) as captured:
            await _generate_with_runtime(
                OllamaTextGenerationRuntime(
                    client,
                    10,
                    LOCAL_MODEL_ALLOWLIST,
                    max_response_bytes=cap,
                ),
                max_output_tokens=1,
            )

    assert stream.iteration_count == 2
    assert stream.closed is True
    assert len(requests) == 1
    outgoing = json.loads(requests[0].content)
    assert outgoing["stream"] is False
    assert outgoing["options"]["num_predict"] == 1
    safe_output = str(captured.value) + caplog.text
    for unsafe in (
        response_fragment.decode(),
        "credential-never-consumed",
        str(cap),
        str(cap + 1),
        LOCAL_MODEL_REFERENCE,
    ):
        assert unsafe not in safe_output


@pytest.mark.asyncio
async def test_ollama_generation_understated_length_cannot_bypass_actual_cap():
    cap = 64
    stream = _RecordingAsyncByteStream((b"x" * cap, b"y"))
    transport, _requests = _generation_transport(
        stream,
        headers=((b"content-length", b"1"),),
    )

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(TextGenerationRuntimeUnavailableError):
            await _generate_with_runtime(
                OllamaTextGenerationRuntime(
                    client,
                    10,
                    LOCAL_MODEL_ALLOWLIST,
                    max_response_bytes=cap,
                )
            )

    assert stream.iteration_count == 2
    assert stream.closed is True


@pytest.mark.asyncio
async def test_ollama_generation_declared_oversize_does_not_iterate_body():
    cap = 64
    stream = _RecordingAsyncByteStream((b"must-not-be-consumed",))
    transport, _requests = _generation_transport(
        stream,
        headers=((b"content-length", str(cap + 1).encode()),),
    )

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(TextGenerationRuntimeUnavailableError):
            await _generate_with_runtime(
                OllamaTextGenerationRuntime(
                    client,
                    10,
                    LOCAL_MODEL_ALLOWLIST,
                    max_response_bytes=cap,
                )
            )

    assert stream.iteration_count == 0
    assert stream.closed is True


@pytest.mark.parametrize(
    ("headers", "private_header"),
    [
        (
            ((b"content-length", b"invalid-private-length"),),
            "invalid-private-length",
        ),
        (((b"content-length", b"-123456"),), "-123456"),
        (((b"content-length", b"123456, 123456"),), "123456, 123456"),
        (
            (
                (b"content-length", b"123456"),
                (b"content-length", b"234567"),
            ),
            "123456",
        ),
    ],
    ids=["malformed", "negative", "comma-ambiguous", "conflicting-duplicate"],
)
@pytest.mark.asyncio
async def test_ollama_generation_rejects_unsafe_length_headers_without_reading(
    headers,
    private_header,
    caplog,
):
    stream = _RecordingAsyncByteStream((b"must-not-be-consumed",))
    transport, _requests = _generation_transport(stream, headers=headers)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(TextGenerationRuntimeUnavailableError) as captured:
            await _generate_with_runtime(
                OllamaTextGenerationRuntime(
                    client,
                    10,
                    LOCAL_MODEL_ALLOWLIST,
                    max_response_bytes=64,
                )
            )

    assert stream.iteration_count == 0
    assert stream.closed is True
    assert private_header not in (str(captured.value) + caplog.text)


@pytest.mark.asyncio
async def test_ollama_generation_non_success_status_does_not_consume_body():
    stream = _RecordingAsyncByteStream((b"large private runtime error",))
    transport, _requests = _generation_transport(
        stream,
        status_code=503,
        headers=((b"content-length", b"999999"),),
    )

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(TextGenerationRuntimeUnavailableError) as captured:
            await _generate_with_runtime(
                OllamaTextGenerationRuntime(
                    client,
                    10,
                    LOCAL_MODEL_ALLOWLIST,
                    max_response_bytes=64,
                )
            )

    assert stream.iteration_count == 0
    assert stream.closed is True
    assert "large private runtime error" not in str(captured.value)
    assert "503" not in str(captured.value)


@pytest.mark.asyncio
async def test_ollama_generation_rejects_compressed_response_before_reading():
    stream = _RecordingAsyncByteStream((b"compressed-private-body",))
    transport, _requests = _generation_transport(
        stream,
        headers=((b"content-encoding", b"gzip"),),
    )

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(TextGenerationRuntimeUnavailableError) as captured:
            await _generate_with_runtime(
                OllamaTextGenerationRuntime(
                    client,
                    10,
                    LOCAL_MODEL_ALLOWLIST,
                    max_response_bytes=64,
                )
            )

    assert stream.iteration_count == 0
    assert stream.closed is True
    assert "gzip" not in str(captured.value)
    assert "compressed-private-body" not in str(captured.value)


@pytest.mark.asyncio
async def test_ollama_generation_malformed_json_remains_generic_unavailable(
    monkeypatch,
):
    response_buffers = _track_generation_response_buffers(monkeypatch)
    stream = _RecordingAsyncByteStream((b'{"done":', b"not-json"))
    transport, _requests = _generation_transport(stream)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(TextGenerationRuntimeUnavailableError) as captured:
            await _generate_with_runtime(
                OllamaTextGenerationRuntime(
                    client,
                    10,
                    LOCAL_MODEL_ALLOWLIST,
                    max_response_bytes=64,
                )
            )

    assert stream.closed is True
    assert "not-json" not in str(captured.value)
    assert len(response_buffers) == 1
    assert response_buffers[0].clear_count == 1
    assert len(response_buffers[0]) == 0


@pytest.mark.asyncio
async def test_ollama_generation_parser_recursion_is_generic_and_clears_body(
    monkeypatch,
):
    private_fragment = "private response fragment"
    parser_detail = "secret nested parser detail"
    body = _generation_response_body(private_fragment)
    response_buffers = _track_generation_response_buffers(monkeypatch)
    observed_payloads: list[bytes] = []

    def fail_parsing(payload):
        observed_payloads.append(bytes(payload))
        raise RecursionError(parser_detail)

    monkeypatch.setattr(ollama_runtime_module.json, "loads", fail_parsing)
    stream = _RecordingAsyncByteStream((body,))
    transport, _requests = _generation_transport(stream)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(TextGenerationRuntimeUnavailableError) as captured:
            await _generate_with_runtime(
                OllamaTextGenerationRuntime(
                    client,
                    10,
                    LOCAL_MODEL_ALLOWLIST,
                    max_response_bytes=len(body),
                )
            )

    assert str(captured.value) == "local text generation is unavailable"
    assert parser_detail not in str(captured.value)
    assert private_fragment not in str(captured.value)
    assert observed_payloads == [body]
    assert stream.closed is True
    assert len(response_buffers) == 1
    assert response_buffers[0].clear_count == 1
    assert len(response_buffers[0]) == 0


@pytest.mark.asyncio
async def test_ollama_generation_deep_bounded_json_fails_closed(monkeypatch):
    private_fragment = "private deeply nested response fragment"
    parser_detail = "secret deterministic recursion detail"
    depth = 128
    body = (
        b"[" * depth
        + json.dumps(private_fragment).encode()
        + b"]" * depth
    )
    response_buffers = _track_generation_response_buffers(monkeypatch)
    observed_payloads: list[bytes] = []

    def fail_nested_parsing(payload):
        observed_payloads.append(bytes(payload))
        raise RecursionError(parser_detail)

    monkeypatch.setattr(
        ollama_runtime_module.json,
        "loads",
        fail_nested_parsing,
    )
    stream = _RecordingAsyncByteStream((body[:97], body[97:]))
    transport, _requests = _generation_transport(stream)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(TextGenerationRuntimeUnavailableError) as captured:
            await _generate_with_runtime(
                OllamaTextGenerationRuntime(
                    client,
                    10,
                    LOCAL_MODEL_ALLOWLIST,
                    max_response_bytes=len(body),
                )
            )

    assert len(body) < MAX_OLLAMA_GENERATION_RESPONSE_BYTES
    assert str(captured.value) == "local text generation is unavailable"
    assert parser_detail not in str(captured.value)
    assert private_fragment not in str(captured.value)
    assert observed_payloads == [body]
    assert stream.closed is True
    assert len(response_buffers) == 1
    assert response_buffers[0].clear_count == 1
    assert len(response_buffers[0]) == 0


@pytest.mark.asyncio
async def test_ollama_generation_invalid_envelope_closes_and_clears_body(
    monkeypatch,
):
    response_buffers = _track_generation_response_buffers(monkeypatch)
    body = b'{"done":true,"message":{"role":"assistant"}}'
    stream = _RecordingAsyncByteStream((body,))
    transport, _requests = _generation_transport(stream)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(TextGenerationRuntimeUnavailableError):
            await _generate_with_runtime(
                OllamaTextGenerationRuntime(
                    client,
                    10,
                    LOCAL_MODEL_ALLOWLIST,
                    max_response_bytes=len(body),
                )
            )

    assert stream.closed is True
    assert len(response_buffers) == 1
    assert response_buffers[0].clear_count == 1
    assert len(response_buffers[0]) == 0


@pytest.mark.asyncio
async def test_ollama_generation_parser_cancellation_propagates(monkeypatch):
    body = _generation_response_body("answer")
    response_buffers = _track_generation_response_buffers(monkeypatch)

    def cancel_parsing(_payload):
        raise asyncio.CancelledError

    monkeypatch.setattr(ollama_runtime_module.json, "loads", cancel_parsing)
    stream = _RecordingAsyncByteStream((body,))
    transport, _requests = _generation_transport(stream)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(asyncio.CancelledError):
            await _generate_with_runtime(
                OllamaTextGenerationRuntime(
                    client,
                    10,
                    LOCAL_MODEL_ALLOWLIST,
                    max_response_bytes=len(body),
                )
            )

    assert stream.closed is True
    assert len(response_buffers) == 1
    assert response_buffers[0].clear_count == 1
    assert len(response_buffers[0]) == 0


@pytest.mark.asyncio
async def test_ollama_generation_response_timeout_closes_and_clears_body(
    monkeypatch,
):
    timeout_detail = "secret response timeout detail"
    response_buffers = _track_generation_response_buffers(monkeypatch)
    stream = _RecordingAsyncByteStream(
        (b'{"done":',),
        failure=httpx.ReadTimeout(timeout_detail),
    )
    transport, _requests = _generation_transport(stream)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(TextGenerationRuntimeUnavailableError) as captured:
            await _generate_with_runtime(
                OllamaTextGenerationRuntime(
                    client,
                    10,
                    LOCAL_MODEL_ALLOWLIST,
                    max_response_bytes=64,
                )
            )

    assert timeout_detail not in str(captured.value)
    assert stream.closed is True
    assert len(response_buffers) == 1
    assert response_buffers[0].clear_count == 1
    assert len(response_buffers[0]) == 0


@pytest.mark.asyncio
async def test_ollama_generation_cancellation_closes_stream_and_propagates(
    monkeypatch,
):
    blocked = asyncio.Event()
    release = asyncio.Event()
    response_buffers = _track_generation_response_buffers(monkeypatch)
    body = _generation_response_body("answer")
    stream = _RecordingAsyncByteStream(
        (body[:8], body[8:]),
        blocked=blocked,
        release=release,
    )
    transport, _requests = _generation_transport(stream)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:11434",
    ) as client:
        runtime = OllamaTextGenerationRuntime(
            client,
            10,
            LOCAL_MODEL_ALLOWLIST,
            max_response_bytes=128,
        )
        task = asyncio.create_task(_generate_with_runtime(runtime))
        await blocked.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert stream.iteration_count == 1
    assert stream.closed is True
    assert len(response_buffers) == 1
    assert response_buffers[0].clear_count == 1
    assert len(response_buffers[0]) == 0


@pytest.mark.parametrize(
    "max_request_bytes",
    [
        None,
        True,
        False,
        "1",
        1.0,
        0,
        -1,
        MAX_OLLAMA_GENERATION_REQUEST_BYTES + 1,
    ],
)
def test_ollama_generation_runtime_rejects_invalid_request_caps(
    max_request_bytes,
):
    with pytest.raises((TypeError, ValueError)):
        OllamaTextGenerationRuntime(
            object(),
            10,
            LOCAL_MODEL_ALLOWLIST,
            max_request_bytes=max_request_bytes,
        )


@pytest.mark.parametrize(
    "max_request_bytes",
    [1, 262_144, MAX_OLLAMA_GENERATION_REQUEST_BYTES],
)
def test_ollama_generation_runtime_accepts_bounded_request_caps(
    max_request_bytes,
):
    runtime = OllamaTextGenerationRuntime(
        object(),
        10,
        LOCAL_MODEL_ALLOWLIST,
        max_request_bytes=max_request_bytes,
    )

    assert runtime.max_request_bytes == max_request_bytes


@pytest.mark.asyncio
async def test_ollama_generation_serializes_exactly_once_into_bounded_chunks(
    monkeypatch,
):
    runtime_reference = 'private/"model\\path-界-🧠:latest'
    messages = (
        TextGenerationMessage(
            role=TextGenerationRole.SYSTEM,
            content='ASCII "quoted" \\ \b\f\n\r\t\u0000\u001f',
        ),
        TextGenerationMessage(
            role=TextGenerationRole.ASSISTANT,
            content="é界🧠",
        ),
        TextGenerationMessage(
            role=TextGenerationRole.USER,
            content='mixed " \\ \n é界🧠',
        ),
    )
    stop_sequences = ["END", '"\\\n', "界🧠"]
    expected_payload = {
        "model": runtime_reference,
        "messages": [
            {"role": message.role.value, "content": message.content}
            for message in messages
        ],
        "stream": False,
        "options": {
            "num_predict": 128,
            "temperature": 0.5,
            "seed": 42,
            "top_p": 0.9,
            "top_k": 40,
            "min_p": 0.05,
            "repeat_penalty": 1.1,
            "repeat_last_n": 64,
            "typical_p": 0.7,
            "presence_penalty": 1.5,
            "frequency_penalty": 0.75,
            "stop": stop_sequences,
        },
    }
    expected_body = json.dumps(
        expected_payload,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    response_body = _generation_response_body("answer")
    requests: list[httpx.Request] = []
    observed_request_streams = []
    observed_chunks: list[tuple[bytes, ...]] = []

    class ObservingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            requests.append(request)
            request_stream = request.stream._stream
            observed_request_streams.append(request_stream)
            observed_chunks.append(request_stream._chunks)
            await request.aread()
            return httpx.Response(200, content=response_body)

    encoder = json.JSONEncoder(
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    iterencode = Mock(wraps=encoder.iterencode)
    encoder.iterencode = iterencode
    encoder_factory = Mock(return_value=encoder)
    monkeypatch.setattr(
        ollama_runtime_module.json,
        "JSONEncoder",
        encoder_factory,
    )

    async with httpx.AsyncClient(
        transport=ObservingTransport(),
        base_url="http://127.0.0.1:11434",
    ) as client:
        stream_call = Mock(wraps=client.stream)
        monkeypatch.setattr(client, "stream", stream_call)
        result = await OllamaTextGenerationRuntime(
            client,
            37,
            (runtime_reference,),
            max_request_bytes=len(expected_body),
        ).generate_text(
            runtime_reference,
            messages,
            max_output_tokens=128,
            temperature=0.5,
            seed=42,
            top_p=0.9,
            top_k=40,
            min_p=0.05,
            repeat_penalty=1.1,
            repeat_last_n=64,
            typical_p=0.7,
            presence_penalty=1.5,
            frequency_penalty=0.75,
            stop_sequences=stop_sequences,
        )

    assert result.content == "answer"
    assert len(requests) == 1
    request = requests[0]
    assert request.content == expected_body
    assert json.loads(request.content) == expected_payload
    assert request.headers["Content-Length"] == str(len(expected_body))
    assert request.headers["Content-Type"] == "application/json"
    assert request.headers["Accept-Encoding"] == "identity"
    assert request.headers.get("Transfer-Encoding") is None
    encoder_factory.assert_called_once_with(
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    iterencode.assert_called_once_with(expected_payload)
    request_kwargs = stream_call.call_args.kwargs
    assert "json" not in request_kwargs
    assert request_kwargs["content"] is observed_request_streams[0]
    assert not isinstance(request_kwargs["content"], bytes)
    assert len(observed_chunks[0]) > 1
    assert sum(len(chunk) for chunk in observed_chunks[0]) == len(expected_body)
    assert all(len(chunk) < len(expected_body) for chunk in observed_chunks[0])
    assert observed_request_streams[0].closed is True
    assert observed_request_streams[0]._chunks == ()


@pytest.mark.asyncio
async def test_ollama_generation_adds_raw_images_only_to_the_new_user_message():
    raw_images = ("cG5n", "anBlZw==")
    messages = (
        TextGenerationMessage(
            role=TextGenerationRole.USER,
            content="historical prompt",
        ),
        TextGenerationMessage(
            role=TextGenerationRole.ASSISTANT,
            content="historical answer",
        ),
        TextGenerationMessage(
            role=TextGenerationRole.USER,
            content="inspect both",
            images=raw_images,
        ),
    )
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=_generation_response_body("answer"))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        result = await OllamaTextGenerationRuntime(
            client,
            10,
            LOCAL_MODEL_ALLOWLIST,
        ).generate_text(
            LOCAL_MODEL_REFERENCE,
            messages,
            max_output_tokens=128,
        )

    assert result.content == "answer"
    assert len(requests) == 1
    assert json.loads(requests[0].content) == {
        "model": LOCAL_MODEL_REFERENCE,
        "messages": [
            {"role": "user", "content": "historical prompt"},
            {"role": "assistant", "content": "historical answer"},
            {
                "role": "user",
                "content": "inspect both",
                "images": ["cG5n", "anBlZw=="],
            },
        ],
        "stream": False,
        "options": {"num_predict": 128},
    }
    assert b"data:" not in requests[0].content


def test_ollama_vision_preflight_matches_exact_serialized_request_boundary():
    messages = (
        TextGenerationMessage(
            role=TextGenerationRole.USER,
            content="inspect",
            images=("cG5n",),
        ),
    )
    payload = {
        "model": LOCAL_MODEL_REFERENCE,
        "messages": [
            {"role": "user", "content": "inspect", "images": ["cG5n"]}
        ],
        "stream": False,
        "options": {"num_predict": 128},
    }
    body = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")

    OllamaTextGenerationRuntime(
        object(),
        10,
        LOCAL_MODEL_ALLOWLIST,
        max_request_bytes=len(body),
    ).preflight_text(
        LOCAL_MODEL_REFERENCE,
        messages,
        max_output_tokens=128,
    )
    with pytest.raises(TextGenerationRequestTooLargeError):
        OllamaTextGenerationRuntime(
            object(),
            10,
            LOCAL_MODEL_ALLOWLIST,
            max_request_bytes=len(body) - 1,
        ).preflight_text(
            LOCAL_MODEL_REFERENCE,
            messages,
            max_output_tokens=128,
        )


@pytest.mark.parametrize("headroom", [0, 1])
@pytest.mark.asyncio
async def test_ollama_generation_accepts_request_at_or_below_cap(headroom):
    expected_payload = {
        "model": LOCAL_MODEL_REFERENCE,
        "messages": [{"role": "user", "content": "bounded prompt"}],
        "stream": False,
        "options": {"num_predict": 128},
    }
    expected_body = json.dumps(
        expected_payload,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            content=_generation_response_body("answer"),
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        result = await OllamaTextGenerationRuntime(
            client,
            10,
            LOCAL_MODEL_ALLOWLIST,
            max_request_bytes=len(expected_body) + headroom,
        ).generate_text(
            LOCAL_MODEL_REFERENCE,
            (
                TextGenerationMessage(
                    role=TextGenerationRole.USER,
                    content="bounded prompt",
                ),
            ),
            max_output_tokens=128,
        )

    assert result.content == "answer"
    assert len(requests) == 1
    assert requests[0].content == expected_body


@pytest.mark.asyncio
async def test_ollama_generation_rejects_request_at_cap_plus_one_before_transport(
    caplog,
):
    private_content = "private-request-fragment"
    expected_body = json.dumps(
        {
            "model": LOCAL_MODEL_REFERENCE,
            "messages": [{"role": "user", "content": private_content}],
            "stream": False,
            "options": {"num_predict": 128},
        },
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            content=_generation_response_body("must-not-run"),
        )

    cap = len(expected_body) - 1
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(TextGenerationRequestTooLargeError) as captured:
            await OllamaTextGenerationRuntime(
                client,
                10,
                LOCAL_MODEL_ALLOWLIST,
                max_request_bytes=cap,
            ).generate_text(
                LOCAL_MODEL_REFERENCE,
                (
                    TextGenerationMessage(
                        role=TextGenerationRole.USER,
                        content=private_content,
                    ),
                ),
                max_output_tokens=128,
            )

    assert requests == []
    safe_output = str(captured.value) + caplog.text
    assert private_content not in safe_output
    assert LOCAL_MODEL_REFERENCE not in safe_output
    assert str(cap) not in safe_output
    assert str(len(expected_body)) not in safe_output


@pytest.mark.asyncio
async def test_ollama_generation_stops_encoding_immediately_on_request_overflow(
    monkeypatch,
):
    transport_calls: list[httpx.Request] = []
    cap = 32

    class OverflowingEncoder:
        def iterencode(self, _payload):
            yield '{"partial":'
            yield '"' + ("x" * cap) + '"'
            raise AssertionError("encoder continued after request overflow")

    async def handler(request: httpx.Request) -> httpx.Response:
        transport_calls.append(request)
        return httpx.Response(200)

    monkeypatch.setattr(
        ollama_runtime_module.json,
        "JSONEncoder",
        Mock(return_value=OverflowingEncoder()),
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(TextGenerationRuntimeUnavailableError):
            await _generate_with_runtime(
                OllamaTextGenerationRuntime(
                    client,
                    10,
                    LOCAL_MODEL_ALLOWLIST,
                    max_request_bytes=cap,
                )
            )

    assert transport_calls == []


@pytest.mark.asyncio
async def test_ollama_generation_rejects_unencodable_request_before_transport(
    caplog,
):
    private_content = "private-\ud800-fragment"
    transport_calls: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        transport_calls.append(request)
        return httpx.Response(200)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:11434",
    ) as client:
        with pytest.raises(TextGenerationRuntimeUnavailableError) as captured:
            await OllamaTextGenerationRuntime(
                client,
                10,
                LOCAL_MODEL_ALLOWLIST,
            ).generate_text(
                LOCAL_MODEL_REFERENCE,
                (
                    TextGenerationMessage(
                        role=TextGenerationRole.USER,
                        content=private_content,
                    ),
                ),
                max_output_tokens=128,
            )

    assert transport_calls == []
    safe_output = str(captured.value) + caplog.text
    assert "private-" not in safe_output
    assert LOCAL_MODEL_REFERENCE not in safe_output


class _BlockingRequestBodyTransport(httpx.AsyncBaseTransport):
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.request_body = None
        self.first_chunk: bytes | None = None

    async def handle_async_request(
        self,
        request: httpx.Request,
    ) -> httpx.Response:
        self.request_body = request.stream._stream
        request_iterator = request.stream.__aiter__()
        self.first_chunk = await anext(request_iterator)
        self.started.set()
        await self.release.wait()
        async for _chunk in request_iterator:
            pass
        return httpx.Response(
            200,
            content=_generation_response_body("answer"),
        )


@pytest.mark.asyncio
async def test_ollama_generation_request_cancellation_closes_body_and_propagates():
    transport = _BlockingRequestBodyTransport()

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1:11434",
    ) as client:
        task = asyncio.create_task(
            _generate_with_runtime(
                OllamaTextGenerationRuntime(
                    client,
                    10,
                    LOCAL_MODEL_ALLOWLIST,
                )
            )
        )
        await transport.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert transport.first_chunk
    assert transport.request_body is not None
    assert transport.request_body.closed is True
    assert transport.request_body._chunks == ()
