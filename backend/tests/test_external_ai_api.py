from types import SimpleNamespace
from uuid import uuid4

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_user
from app.api.v1.external_ai import router
from app.exceptions.handlers import register_exception_handlers
from app.external_ai.service import ExternalAIService
from app.external_ai.vault import EncryptedProviderVault
from app.external_ai.evidence import ExternalVerificationEvidence
from app.external_ai.contracts import ExternalProviderKind
from app.ai.routing import ModelTask


SECRET = "api-key-that-must-never-be-returned"


def _api(tmp_path, *, configured=True):
    application = FastAPI()
    register_exception_handlers(application)
    application.include_router(router, prefix="/api/v1")
    owner = SimpleNamespace(id=uuid4())
    application.dependency_overrides[get_current_user] = lambda: owner

    async def handler(request: httpx.Request):
        if request.url.path.endswith("/models"):
            return httpx.Response(
                200,
                json={"data": [{"id": "candidate-model"}]},
            )
        return httpx.Response(404)

    if configured:
        vault = EncryptedProviderVault(tmp_path / "external-ai")
        evidence = vault.register_verification_evidence(
            ExternalVerificationEvidence(
                provider_kind=ExternalProviderKind.OPENAI,
                model_id="verified-model",
                tasks=frozenset({ModelTask.REASONING}),
                benchmark_artifact_sha256="f" * 64,
                complete_category=True,
                passed=True,
                measured_quality=95,
                measured_latency_ms=10,
                stability_rate=1,
                context_window=32_768,
                input_cost_micros_per_million_tokens=100,
                output_cost_micros_per_million_tokens=200,
            )
        )
        service = ExternalAIService(
            vault,
            httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        application.state.external_ai_service = service
    else:
        service = None
        evidence = None
        application.state.external_ai_service = None
    return TestClient(application), service, evidence


def _provider_body(evidence, *, include_key=True, verified=True):
    return {
        "kind": "openai",
        **({"api_key": SECRET} if include_key else {}),
        "enabled": True,
        "free_tier": False,
        "priority": 10,
        "timeout_seconds": 5,
        "rate_limit_requests_per_minute": 20,
        "spending_limit_micros": 1_000_000,
        "quota_remaining_tokens": 100_000,
        "models": [
            {
                "model_id": "verified-model",
                "tasks": ["reasoning"],
                "verified": verified,
                **(
                    {"verification_evidence_sha256": evidence}
                    if verified
                    else {}
                ),
                "measured_quality": 95,
                "input_cost_micros_per_million_tokens": 100,
                "output_cost_micros_per_million_tokens": 200,
            }
        ],
    }


def test_external_ai_settings_fail_closed_when_vault_is_unconfigured():
    client, _service, _evidence = _api(None, configured=False)

    read = client.get("/api/v1/external-ai/settings")
    update = client.put("/api/v1/external-ai/settings", json={"enabled": True})

    assert read.status_code == 200
    assert read.json() == {
        "configured": False,
        "global_enabled": False,
        "providers": [],
        "supported_provider_kinds": ["openai", "anthropic", "google"],
    }
    assert update.status_code == 409


def test_external_ai_provider_key_is_write_only_and_unknown_models_stay_unadmitted(tmp_path):
    client, service, evidence = _api(tmp_path)
    assert service is not None

    created = client.put(
        "/api/v1/external-ai/providers/openai-primary",
        json=_provider_body(evidence),
    )

    assert created.status_code == 200
    serialized = created.text
    assert SECRET not in serialized
    provider = created.json()["providers"][0]
    assert provider["key_configured"] is True
    assert provider["models"][0]["context_window"] == 32_768
    assert provider["models"][0]["stability_rate"] == 1
    assert set(provider) == {
        "provider_id",
        "kind",
        "enabled",
        "key_configured",
        "free_tier",
        "priority",
        "timeout_seconds",
        "rate_limit_requests_per_minute",
        "spending_limit_micros",
        "spent_micros",
        "quota_remaining_tokens",
        "status",
        "models",
    }
    assert SECRET.encode() not in service.vault.vault_path.read_bytes()

    enabled = client.put("/api/v1/external-ai/settings", json={"enabled": True})
    assert enabled.json()["global_enabled"] is True
    assert enabled.json()["providers"][0]["status"] == "ready"

    discovered = client.post(
        "/api/v1/external-ai/providers/openai-primary/discover"
    )
    assert discovered.status_code == 200
    assert discovered.json() == {
        "provider_id": "openai-primary",
        "discovered_model_ids": ["candidate-model"],
        "production_admitted": False,
    }


def test_external_ai_provider_update_retains_key_and_validation_is_sanitized(tmp_path):
    client, service, evidence = _api(tmp_path)
    assert service is not None
    client.put(
        "/api/v1/external-ai/providers/openai-primary",
        json=_provider_body(evidence),
    )

    updated = client.put(
        "/api/v1/external-ai/providers/openai-primary",
        json=_provider_body(evidence, include_key=False),
    )
    assert updated.status_code == 200
    assert service.vault.snapshot().providers[0].api_key == SECRET

    invalid_secret = "short-secret"
    invalid = client.put(
        "/api/v1/external-ai/providers/bad-provider",
        json={**_provider_body(evidence), "api_key": invalid_secret},
    )
    assert invalid.status_code == 422
    assert invalid_secret not in invalid.text

    missing_evidence = client.put(
        "/api/v1/external-ai/providers/bad-provider",
        json=_provider_body(evidence, verified=False) | {
            "models": [
                {
                    "model_id": "bad-model",
                    "tasks": ["reasoning"],
                    "verified": True,
                }
            ]
        },
    )
    assert missing_evidence.status_code == 422
    assert missing_evidence.json()["error"]["message"] == (
        "External provider configuration is invalid"
    )

    unregistered_evidence = client.put(
        "/api/v1/external-ai/providers/unverified-provider",
        json=_provider_body("d" * 64),
    )
    assert unregistered_evidence.status_code == 422
    assert unregistered_evidence.json()["error"]["message"] == (
        "External provider configuration is invalid"
    )


def test_external_ai_provider_delete_is_exact_and_owner_authenticated(tmp_path):
    client, _service, evidence = _api(tmp_path)
    client.put(
        "/api/v1/external-ai/providers/openai-primary",
        json=_provider_body(evidence),
    )

    deleted = client.delete(
        "/api/v1/external-ai/providers/openai-primary"
    )
    missing = client.delete(
        "/api/v1/external-ai/providers/openai-primary"
    )

    assert deleted.status_code == 200
    assert deleted.json()["providers"] == []
    assert missing.status_code == 404
