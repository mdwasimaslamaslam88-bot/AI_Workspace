import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from app.ai.generation import TextGenerationMessage, TextGenerationRole
from app.ai.routing import ModelTask
from app.external_ai.contracts import (
    ExternalModelPolicy,
    ExternalProviderConfig,
    ExternalProviderKind,
)
from app.external_ai.service import ExternalAIService, ExternalAIUnavailableError
from app.external_ai.vault import EncryptedProviderVault
from app.external_ai.evidence import ExternalVerificationEvidence


KEY = "provider-secret-never-log-this"


def _model(model_id, *, quality=90, input_cost=1_000_000, output_cost=2_000_000, verified=True):
    return ExternalModelPolicy(
        model_id=model_id,
        tasks=frozenset({ModelTask.REASONING}),
        verified=verified,
        verification_evidence_sha256=("b" * 64 if verified else None),
        measured_quality=quality,
        measured_latency_ms=10,
        stability_rate=1,
        context_window=32_768,
        input_cost_micros_per_million_tokens=input_cost,
        output_cost_micros_per_million_tokens=output_cost,
    )


def _vault(tmp_path, *configs):
    vault = EncryptedProviderVault(tmp_path / "external-ai")
    for config in configs:
        admitted_models = []
        for model in config.models:
            if not model.verified:
                admitted_models.append(model)
                continue
            digest = vault.register_verification_evidence(
                ExternalVerificationEvidence(
                    provider_kind=config.kind,
                    model_id=model.model_id,
                    tasks=model.tasks,
                    benchmark_artifact_sha256="f" * 64,
                    complete_category=True,
                    passed=True,
                    measured_quality=model.measured_quality,
                    measured_latency_ms=model.measured_latency_ms,
                    stability_rate=model.stability_rate,
                    context_window=model.context_window,
                    input_cost_micros_per_million_tokens=model.input_cost_micros_per_million_tokens,
                    output_cost_micros_per_million_tokens=model.output_cost_micros_per_million_tokens,
                )
            )
            admitted_models.append(
                replace(model, verification_evidence_sha256=digest)
            )
        admitted = replace(config, models=tuple(admitted_models))
        vault.upsert_provider(admitted, api_key=KEY + config.provider_id)
    vault.set_global_enabled(True)
    return vault


def test_provider_policy_prefers_free_then_cheapest_and_rejects_unverified(tmp_path):
    paid = ExternalProviderConfig(
        "paid",
        ExternalProviderKind.OPENAI,
        enabled=True,
        models=(_model("paid-strong", quality=99, input_cost=1, output_cost=1),),
    )
    free = ExternalProviderConfig(
        "free",
        ExternalProviderKind.GOOGLE,
        enabled=True,
        free_tier=True,
        models=(
            _model("free-unverified", verified=False),
            _model("free-verified", quality=80, input_cost=100, output_cost=100),
        ),
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: httpx.Response(500)))
    service = ExternalAIService(_vault(tmp_path, paid, free), client)

    choices = service.choices(ModelTask.REASONING)

    assert [(item.provider.config.provider_id, item.model.model_id) for item in choices] == [
        ("free", "free-verified"),
        ("paid", "paid-strong"),
    ]


@pytest.mark.asyncio
async def test_openai_provider_request_keeps_key_out_of_prompt_and_tracks_cost(tmp_path):
    observed = {}

    def handler(request: httpx.Request):
        observed["authorization"] = request.headers["authorization"]
        observed["body"] = request.content
        return httpx.Response(
            200,
            json={
                "output_text": "verified external result",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )

    config = ExternalProviderConfig(
        "openai-primary",
        ExternalProviderKind.OPENAI,
        enabled=True,
        spending_limit_micros=1_000_000,
        models=(_model("verified-model"),),
    )
    vault = _vault(tmp_path, config)
    service = ExternalAIService(
        vault,
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    messages = (
        TextGenerationMessage(TextGenerationRole.SYSTEM, "Keep policy."),
        TextGenerationMessage(TextGenerationRole.USER, "Solve this."),
    )

    result = await service.generate(
        ModelTask.REASONING,
        messages,
        max_output_tokens=100,
    )

    assert result.content == "verified external result"
    assert result.cost_micros == 20
    assert observed["authorization"] == f"Bearer {KEY}openai-primary"
    assert (KEY + "openai-primary").encode() not in observed["body"]
    assert json.loads(observed["body"])["store"] is False
    stored = vault.snapshot().providers[0]
    assert stored.spent_micros == 20


@pytest.mark.asyncio
async def test_external_policy_honors_global_quota_spending_and_rate_limits(tmp_path):
    config = ExternalProviderConfig(
        "quota-provider",
        ExternalProviderKind.ANTHROPIC,
        enabled=True,
        quota_remaining_tokens=0,
        spending_limit_micros=10,
        rate_limit_requests_per_minute=1,
        models=(_model("verified-model"),),
    )
    service = ExternalAIService(
        _vault(tmp_path, config),
        httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: httpx.Response(500))),
    )

    with pytest.raises(ExternalAIUnavailableError):
        await service.generate(
            ModelTask.REASONING,
            (TextGenerationMessage(TextGenerationRole.USER, "prompt"),),
            max_output_tokens=10,
        )


@pytest.mark.asyncio
async def test_parallel_requests_reserve_spending_budget_before_network_use(tmp_path):
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    request_count = 0

    async def handler(_request: httpx.Request):
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            first_started.set()
            await release_first.wait()
        return httpx.Response(
            200,
            json={
                "output_text": "bounded result",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    config = ExternalProviderConfig(
        "budget-provider",
        ExternalProviderKind.OPENAI,
        enabled=True,
        spending_limit_micros=1_060,
        models=(
            _model(
                "budget-model",
                input_cost=1_000_000,
                output_cost=1_000_000,
            ),
        ),
    )
    service = ExternalAIService(
        _vault(tmp_path, config),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    messages = (TextGenerationMessage(TextGenerationRole.USER, "prompt"),)
    first = asyncio.create_task(
        service.generate(ModelTask.REASONING, messages, max_output_tokens=10)
    )
    await first_started.wait()

    with pytest.raises(ExternalAIUnavailableError):
        await service.generate(ModelTask.REASONING, messages, max_output_tokens=10)
    assert request_count == 1

    release_first.set()
    assert (await first).content == "bounded result"
    assert (
        await service.generate(ModelTask.REASONING, messages, max_output_tokens=10)
    ).content == "bounded result"
    assert request_count == 2
