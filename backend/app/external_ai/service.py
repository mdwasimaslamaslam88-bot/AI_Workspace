from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass
import json
import math
import time
from typing import Any
from urllib.parse import quote

import httpx

from app.ai.generation import TextGenerationMessage, TextGenerationRole
from app.ai.routing import ModelTask
from app.external_ai.contracts import (
    ExternalGenerationResult,
    ExternalModelPolicy,
    ExternalProviderKind,
    ExternalProviderRecord,
    ExternalProviderStatus,
    MAX_EXTERNAL_RESPONSE_BYTES,
)
from app.external_ai.vault import EncryptedProviderVault


_PROVIDER_ORIGINS = {
    ExternalProviderKind.OPENAI: "https://api.openai.com",
    ExternalProviderKind.ANTHROPIC: "https://api.anthropic.com",
    ExternalProviderKind.GOOGLE: "https://generativelanguage.googleapis.com",
}


class ExternalAIUnavailableError(RuntimeError):
    """No configured external provider can safely satisfy the request."""


@dataclass(frozen=True, slots=True)
class ExternalProviderView:
    provider_id: str
    kind: ExternalProviderKind
    enabled: bool
    key_configured: bool
    free_tier: bool
    priority: int
    timeout_seconds: float
    rate_limit_requests_per_minute: int
    spending_limit_micros: int
    spent_micros: int
    quota_remaining_tokens: int | None
    status: ExternalProviderStatus
    models: tuple[ExternalModelPolicy, ...]


@dataclass(frozen=True, slots=True)
class ExternalModelChoice:
    provider: ExternalProviderRecord
    model: ExternalModelPolicy


class _ProviderRateLimiter:
    def __init__(self) -> None:
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def acquire(self, provider_id: str, limit: int) -> bool:
        now = time.monotonic()
        async with self._lock:
            requests = self._requests[provider_id]
            while requests and requests[0] <= now - 60:
                requests.popleft()
            if len(requests) >= limit:
                return False
            requests.append(now)
            return True


class ExternalAIService:
    """Local-policy-owned provider execution; API keys never enter prompts."""

    def __init__(
        self,
        vault: EncryptedProviderVault,
        client: httpx.AsyncClient,
    ) -> None:
        self.vault = vault
        self.client = client
        self._rate_limiter = _ProviderRateLimiter()
        self._budget_lock = asyncio.Lock()
        self._reservations: dict[str, tuple[int, int]] = {}

    @staticmethod
    def create_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(30.0),
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
        )

    async def close(self) -> None:
        await self.client.aclose()

    def global_enabled(self) -> bool:
        return self.vault.snapshot().global_enabled

    def provider_views(self) -> tuple[ExternalProviderView, ...]:
        state = self.vault.snapshot()
        return tuple(
            self._view(state.global_enabled, provider)
            for provider in state.providers
        )

    @staticmethod
    def _view(
        global_enabled: bool,
        provider: ExternalProviderRecord,
    ) -> ExternalProviderView:
        config = provider.config
        if not global_enabled or not config.enabled:
            status = ExternalProviderStatus.DISABLED
        elif config.quota_remaining_tokens == 0:
            status = ExternalProviderStatus.QUOTA_EXHAUSTED
        elif (
            config.spending_limit_micros > 0
            and provider.spent_micros >= config.spending_limit_micros
        ):
            status = ExternalProviderStatus.SPENDING_LIMIT_REACHED
        else:
            status = ExternalProviderStatus.READY
        return ExternalProviderView(
            provider_id=config.provider_id,
            kind=config.kind,
            enabled=config.enabled,
            key_configured=True,
            free_tier=config.free_tier,
            priority=config.priority,
            timeout_seconds=config.timeout_seconds,
            rate_limit_requests_per_minute=(
                config.rate_limit_requests_per_minute
            ),
            spending_limit_micros=config.spending_limit_micros,
            spent_micros=provider.spent_micros,
            quota_remaining_tokens=config.quota_remaining_tokens,
            status=status,
            models=config.models,
        )

    def choices(
        self,
        task: ModelTask,
        *,
        excluded_provider_ids: frozenset[str] = frozenset(),
        required_context_tokens: int = 0,
    ) -> tuple[ExternalModelChoice, ...]:
        state = self.vault.snapshot()
        if not state.global_enabled:
            return ()
        choices = []
        for provider in state.providers:
            config = provider.config
            if (
                not config.enabled
                or config.provider_id in excluded_provider_ids
                or config.quota_remaining_tokens == 0
                or (
                    config.spending_limit_micros > 0
                    and provider.spent_micros >= config.spending_limit_micros
                )
            ):
                continue
            for model in config.models:
                if (
                    model.verified
                    and task in model.tasks
                    and (
                        required_context_tokens == 0
                        or model.context_window >= required_context_tokens
                    )
                ):
                    choices.append(ExternalModelChoice(provider, model))
        return tuple(
            sorted(
                choices,
                key=lambda choice: (
                    not choice.provider.config.free_tier,
                    choice.model.input_cost_micros_per_million_tokens
                    + choice.model.output_cost_micros_per_million_tokens,
                    -choice.model.measured_quality,
                    -choice.model.stability_rate,
                    choice.model.measured_latency_ms,
                    choice.provider.config.priority,
                    choice.provider.config.provider_id,
                    choice.model.model_id,
                ),
            )
        )

    async def generate(
        self,
        task: ModelTask,
        messages: tuple[TextGenerationMessage, ...],
        *,
        max_output_tokens: int,
        excluded_provider_ids: frozenset[str] = frozenset(),
    ) -> ExternalGenerationResult:
        if not messages:
            raise ValueError("external generation messages must not be empty")
        if (
            isinstance(max_output_tokens, bool)
            or not isinstance(max_output_tokens, int)
            or not 1 <= max_output_tokens <= 8192
        ):
            raise ValueError("external output token bound is invalid")
        for choice in self.choices(
            task,
            excluded_provider_ids=excluded_provider_ids,
        ):
            result = await self._try_choice(
                choice,
                messages,
                max_output_tokens=max_output_tokens,
            )
            if result is not None:
                return result
        raise ExternalAIUnavailableError(
            "no configured external provider satisfies policy"
        )

    async def generate_selected(
        self,
        task: ModelTask,
        provider_id: str,
        model_id: str,
        messages: tuple[TextGenerationMessage, ...],
        *,
        max_output_tokens: int,
    ) -> ExternalGenerationResult:
        choice = next(
            (
                candidate
                for candidate in self.choices(task)
                if candidate.provider.config.provider_id == provider_id
                and candidate.model.model_id == model_id
            ),
            None,
        )
        if choice is None:
            raise ExternalAIUnavailableError(
                "selected external provider is not policy eligible"
            )
        result = await self._try_choice(
            choice,
            messages,
            max_output_tokens=max_output_tokens,
        )
        if result is None:
            raise ExternalAIUnavailableError(
                "selected external provider is unavailable"
            )
        return result

    async def _try_choice(
        self,
        choice: ExternalModelChoice,
        messages: tuple[TextGenerationMessage, ...],
        *,
        max_output_tokens: int,
    ) -> ExternalGenerationResult | None:
        config = choice.provider.config
        if not await self._rate_limiter.acquire(
            config.provider_id,
            config.rate_limit_requests_per_minute,
        ):
            return None
        maximum_input_tokens = max(
            1,
            1_024 + sum(
                len(message.content.encode("utf-8")) + 16
                for message in messages
            ),
        )
        maximum_cost = self._cost(
            choice.model,
            maximum_input_tokens,
            max_output_tokens,
        )
        maximum_tokens = maximum_input_tokens + max_output_tokens
        reserved_choice = await self._reserve_choice(
            choice,
            cost_micros=maximum_cost,
            token_count=maximum_tokens,
        )
        if reserved_choice is None:
            return None
        settled = False
        try:
            try:
                content, input_tokens, output_tokens = await self._generate_choice(
                    reserved_choice,
                    messages,
                    max_output_tokens=max_output_tokens,
                )
            except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError):
                return None
            cost = self._cost(reserved_choice.model, input_tokens, output_tokens)
            await self._settle_reservation(
                config.provider_id,
                reserved_cost_micros=maximum_cost,
                reserved_tokens=maximum_tokens,
                actual_cost_micros=cost,
                actual_tokens=input_tokens + output_tokens,
            )
            settled = True
            return ExternalGenerationResult(
                content=content,
                provider_id=config.provider_id,
                model_id=reserved_choice.model.model_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_micros=cost,
            )
        finally:
            if not settled:
                await self._settle_reservation(
                    config.provider_id,
                    reserved_cost_micros=maximum_cost,
                    reserved_tokens=maximum_tokens,
                )

    async def _reserve_choice(
        self,
        choice: ExternalModelChoice,
        *,
        cost_micros: int,
        token_count: int,
    ) -> ExternalModelChoice | None:
        provider_id = choice.provider.config.provider_id
        async with self._budget_lock:
            state = self.vault.snapshot()
            if not state.global_enabled:
                return None
            provider = next(
                (
                    item
                    for item in state.providers
                    if item.config.provider_id == provider_id
                ),
                None,
            )
            if provider is None or not provider.config.enabled:
                return None
            model = next(
                (item for item in provider.config.models if item == choice.model),
                None,
            )
            if model is None or not model.verified or token_count > model.context_window:
                return None
            reserved_cost, reserved_tokens = self._reservations.get(
                provider_id,
                (0, 0),
            )
            config = provider.config
            if (
                config.spending_limit_micros > 0
                and provider.spent_micros + reserved_cost + cost_micros
                > config.spending_limit_micros
            ):
                return None
            if (
                config.quota_remaining_tokens is not None
                and reserved_tokens + token_count
                > config.quota_remaining_tokens
            ):
                return None
            self._reservations[provider_id] = (
                reserved_cost + cost_micros,
                reserved_tokens + token_count,
            )
            return ExternalModelChoice(provider, model)

    async def _settle_reservation(
        self,
        provider_id: str,
        *,
        reserved_cost_micros: int,
        reserved_tokens: int,
        actual_cost_micros: int | None = None,
        actual_tokens: int | None = None,
    ) -> None:
        async with self._budget_lock:
            current_cost, current_tokens = self._reservations.get(
                provider_id,
                (0, 0),
            )
            remaining = (
                max(0, current_cost - reserved_cost_micros),
                max(0, current_tokens - reserved_tokens),
            )
            if remaining == (0, 0):
                self._reservations.pop(provider_id, None)
            else:
                self._reservations[provider_id] = remaining
            if actual_cost_micros is not None and actual_tokens is not None:
                self.vault.record_usage(
                    provider_id,
                    cost_micros=actual_cost_micros,
                    token_count=actual_tokens,
                )

    async def health(self, provider_id: str) -> ExternalProviderStatus:
        provider = self._provider(provider_id)
        view = self._view(self.vault.snapshot().global_enabled, provider)
        if view.status is not ExternalProviderStatus.READY:
            return view.status
        try:
            await self._discover_provider_models(provider)
        except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError):
            return ExternalProviderStatus.UNAVAILABLE
        return ExternalProviderStatus.READY

    async def discover_models(self, provider_id: str) -> tuple[str, ...]:
        return await self._discover_provider_models(self._provider(provider_id))

    def _provider(self, provider_id: str) -> ExternalProviderRecord:
        return next(
            (
                provider
                for provider in self.vault.snapshot().providers
                if provider.config.provider_id == provider_id
            ),
            None,
        ) or (_raise_provider_missing())

    async def _discover_provider_models(
        self,
        provider: ExternalProviderRecord,
    ) -> tuple[str, ...]:
        kind = provider.config.kind
        path = (
            "/v1/models"
            if kind in {ExternalProviderKind.OPENAI, ExternalProviderKind.ANTHROPIC}
            else "/v1beta/models"
        )
        payload = await self._request_json(
            "GET",
            _PROVIDER_ORIGINS[kind] + path,
            headers=self._headers(provider),
            timeout=provider.config.timeout_seconds,
        )
        raw_models = payload.get("data") if kind in {
            ExternalProviderKind.OPENAI,
            ExternalProviderKind.ANTHROPIC,
        } else payload.get("models")
        if not isinstance(raw_models, list):
            raise ValueError("provider model discovery response is invalid")
        identifiers = []
        for item in raw_models[:512]:
            if not isinstance(item, dict):
                continue
            identifier = item.get("id") if kind is not ExternalProviderKind.GOOGLE else item.get("name")
            if isinstance(identifier, str) and 1 <= len(identifier) <= 128:
                if kind is ExternalProviderKind.GOOGLE and identifier.startswith("models/"):
                    identifier = identifier[7:]
                identifiers.append(identifier)
        return tuple(sorted(set(identifiers)))

    async def _generate_choice(
        self,
        choice: ExternalModelChoice,
        messages: tuple[TextGenerationMessage, ...],
        *,
        max_output_tokens: int,
    ) -> tuple[str, int, int]:
        provider = choice.provider
        kind = provider.config.kind
        system = "\n\n".join(
            message.content
            for message in messages
            if message.role is TextGenerationRole.SYSTEM
        )
        conversation = [
            {
                "role": message.role.value,
                "content": message.content,
            }
            for message in messages
            if message.role is not TextGenerationRole.SYSTEM
        ]
        if kind is ExternalProviderKind.OPENAI:
            body = {
                "model": choice.model.model_id,
                "input": (
                    ([{"role": "system", "content": system}] if system else [])
                    + conversation
                ),
                "max_output_tokens": max_output_tokens,
                "store": False,
            }
            payload = await self._request_json(
                "POST",
                _PROVIDER_ORIGINS[kind] + "/v1/responses",
                headers=self._headers(provider),
                body=body,
                timeout=provider.config.timeout_seconds,
            )
            content = payload.get("output_text")
            if not isinstance(content, str):
                content = "".join(
                    block.get("text", "")
                    for item in payload.get("output", [])
                    if isinstance(item, dict)
                    for block in item.get("content", [])
                    if isinstance(block, dict) and block.get("type") == "output_text"
                )
            usage = payload.get("usage", {})
            return (
                _validated_content(content),
                _usage(usage, "input_tokens"),
                _usage(usage, "output_tokens"),
            )
        if kind is ExternalProviderKind.ANTHROPIC:
            body = {
                "model": choice.model.model_id,
                "max_tokens": max_output_tokens,
                "messages": conversation,
                **({"system": system} if system else {}),
            }
            payload = await self._request_json(
                "POST",
                _PROVIDER_ORIGINS[kind] + "/v1/messages",
                headers=self._headers(provider),
                body=body,
                timeout=provider.config.timeout_seconds,
            )
            content = "".join(
                block.get("text", "")
                for block in payload.get("content", [])
                if isinstance(block, dict) and block.get("type") == "text"
            )
            usage = payload.get("usage", {})
            return (
                _validated_content(content),
                _usage(usage, "input_tokens"),
                _usage(usage, "output_tokens"),
            )
        body = {
            "contents": [
                {
                    "role": "model" if item["role"] == "assistant" else "user",
                    "parts": [{"text": item["content"]}],
                }
                for item in conversation
            ],
            "generationConfig": {"maxOutputTokens": max_output_tokens},
            **(
                {"systemInstruction": {"parts": [{"text": system}]}}
                if system
                else {}
            ),
        }
        model_path = quote(choice.model.model_id, safe="._-")
        payload = await self._request_json(
            "POST",
            _PROVIDER_ORIGINS[kind]
            + f"/v1beta/models/{model_path}:generateContent",
            headers=self._headers(provider),
            body=body,
            timeout=provider.config.timeout_seconds,
        )
        candidates = payload.get("candidates", [])
        content = "".join(
            part.get("text", "")
            for candidate in candidates[:1]
            if isinstance(candidate, dict)
            for part in candidate.get("content", {}).get("parts", [])
            if isinstance(part, dict)
        )
        usage = payload.get("usageMetadata", {})
        return (
            _validated_content(content),
            _usage(usage, "promptTokenCount"),
            _usage(usage, "candidatesTokenCount"),
        )

    @staticmethod
    def _headers(provider: ExternalProviderRecord) -> dict[str, str]:
        kind = provider.config.kind
        if kind is ExternalProviderKind.OPENAI:
            return {
                "Authorization": f"Bearer {provider.api_key}",
                "Content-Type": "application/json",
            }
        if kind is ExternalProviderKind.ANTHROPIC:
            return {
                "x-api-key": provider.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            }
        return {
            "x-goog-api-key": provider.api_key,
            "Content-Type": "application/json",
        }

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request = self.client.build_request(
            method,
            url,
            headers=headers,
            content=(
                json.dumps(body, separators=(",", ":")).encode("utf-8")
                if body is not None
                else None
            ),
        )
        async with asyncio.timeout(timeout):
            response = await self.client.send(request, stream=True)
            try:
                response.raise_for_status()
                chunks = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_EXTERNAL_RESPONSE_BYTES:
                        raise ValueError("external provider response exceeded its bound")
                    chunks.append(chunk)
            finally:
                await response.aclose()
        try:
            payload = json.loads(b"".join(chunks))
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise ValueError("external provider response is invalid") from exc
        if not isinstance(payload, dict):
            raise ValueError("external provider response is invalid")
        return payload

    @staticmethod
    def _cost(
        model: ExternalModelPolicy,
        input_tokens: int,
        output_tokens: int,
    ) -> int:
        numerator = (
            input_tokens * model.input_cost_micros_per_million_tokens
            + output_tokens * model.output_cost_micros_per_million_tokens
        )
        return math.ceil(numerator / 1_000_000)


def _validated_content(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 262_144:
        raise ValueError("external provider content is invalid")
    return value


def _usage(payload: object, field: str) -> int:
    if not isinstance(payload, dict):
        return 0
    value = payload.get(field, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10_000_000 else 0


def _raise_provider_missing():
    raise KeyError("external provider not found")
