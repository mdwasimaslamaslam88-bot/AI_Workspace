from unittest.mock import Mock

import httpx
import pytest

import app.clients.ollama as ollama_client_module
from app.ai.catalog import ModelCapability, ModelRuntimeUnavailableError
from app.clients.ollama import create_ollama_client
from app.core.config import Settings
from app.runtimes.ollama import OllamaModelDiscoveryRuntime


@pytest.mark.asyncio
async def test_ollama_discovery_uses_only_inventory_and_hides_raw_reference():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
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
        (model,) = await OllamaModelDiscoveryRuntime(client).discover_models()

    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/api/tags")
    ]
    assert model.reference == "/private/models/secret:14b"
    assert model.display_name == "LocalFamily 14B"
    assert model.family == "LocalFamily"
    assert model.parameter_class == "14B"
    assert model.quantization == "Q4_K_M"
    assert model.capabilities == (
        ModelCapability.CHAT,
        ModelCapability.TEXT_GENERATION,
    )


@pytest.mark.asyncio
async def test_ollama_unsafe_metadata_is_not_promoted_to_public_fields():
    async def handler(_request: httpx.Request) -> httpx.Response:
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
        (model,) = await OllamaModelDiscoveryRuntime(client).discover_models()

    assert model.display_name == "Local text model"
    assert model.family is None
    assert model.parameter_class is None
    assert model.quantization is None


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
