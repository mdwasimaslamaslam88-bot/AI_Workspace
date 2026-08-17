from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math
from typing import Protocol, runtime_checkable

from app.ai.catalog import ResolvedModel


class TextGenerationRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class TextGenerationRuntimeUnavailableError(RuntimeError):
    """A local text runtime could not produce a safe response."""


class TextGenerationRuntimeUnsupportedError(RuntimeError):
    """No local text-generation adapter is registered for a resolved model."""


@dataclass(frozen=True, slots=True)
class TextGenerationMessage:
    role: TextGenerationRole
    content: str

    def __post_init__(self) -> None:
        if not isinstance(self.role, TextGenerationRole):
            raise TypeError("generation message role must be a TextGenerationRole")
        if not isinstance(self.content, str):
            raise TypeError("generation message content must be a string")


@dataclass(frozen=True, slots=True)
class TextGenerationResult:
    content: str

    def __post_init__(self) -> None:
        if not isinstance(self.content, str):
            raise TypeError("generated content must be a string")
        if not self.content.strip():
            raise ValueError("generated content must not be blank")


@runtime_checkable
class TextGenerationRuntime(Protocol):
    runtime_id: str

    async def generate_text(
        self,
        runtime_reference: str,
        messages: tuple[TextGenerationMessage, ...],
        *,
        max_output_tokens: int,
        temperature: float | None = None,
        seed: int | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        min_p: float | None = None,
        repeat_penalty: float | None = None,
        repeat_last_n: int | None = None,
    ) -> TextGenerationResult: ...


class TextGenerationRouter:
    def __init__(
        self,
        runtimes: tuple[TextGenerationRuntime, ...] = (),
    ) -> None:
        self._runtimes: dict[str, TextGenerationRuntime] = {}
        for runtime in runtimes:
            if runtime.runtime_id in self._runtimes:
                raise ValueError(
                    f"duplicate text-generation runtime_id: {runtime.runtime_id}"
                )
            self._runtimes[runtime.runtime_id] = runtime

    async def generate(
        self,
        model: ResolvedModel,
        messages: tuple[TextGenerationMessage, ...],
        *,
        max_output_tokens: int,
        temperature: float | None = None,
        seed: int | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        min_p: float | None = None,
        repeat_penalty: float | None = None,
        repeat_last_n: int | None = None,
    ) -> TextGenerationResult:
        if not isinstance(model, ResolvedModel):
            raise TypeError("model must be a ResolvedModel")
        if not messages:
            raise ValueError("generation messages must not be empty")
        if isinstance(max_output_tokens, bool) or not isinstance(
            max_output_tokens, int
        ):
            raise TypeError("max_output_tokens must be an integer")
        if max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")
        if temperature is not None:
            if isinstance(temperature, bool) or not isinstance(
                temperature,
                (int, float),
            ):
                raise TypeError("temperature must be numeric")
            try:
                is_finite_temperature = math.isfinite(temperature)
            except OverflowError:
                is_finite_temperature = False
            if not is_finite_temperature or not 0.0 <= temperature <= 2.0:
                raise ValueError(
                    "temperature must be finite and between 0.0 and 2.0"
                )
        if seed is not None:
            if isinstance(seed, bool) or not isinstance(seed, int):
                raise TypeError("seed must be an integer")
            if not 0 <= seed <= 2_147_483_647:
                raise ValueError(
                    "seed must be between 0 and 2147483647"
                )
        if top_p is not None:
            if isinstance(top_p, bool) or not isinstance(
                top_p,
                (int, float),
            ):
                raise TypeError("top_p must be numeric")
            try:
                is_finite_top_p = math.isfinite(top_p)
            except OverflowError:
                is_finite_top_p = False
            if not is_finite_top_p or not 0.0 <= top_p <= 1.0:
                raise ValueError(
                    "top_p must be finite and between 0.0 and 1.0"
                )
        if top_k is not None:
            if isinstance(top_k, bool) or not isinstance(top_k, int):
                raise TypeError("top_k must be an integer")
            if not 1 <= top_k <= 100:
                raise ValueError("top_k must be between 1 and 100")
        if min_p is not None:
            if isinstance(min_p, bool) or not isinstance(
                min_p,
                (int, float),
            ):
                raise TypeError("min_p must be numeric")
            try:
                is_finite_min_p = math.isfinite(min_p)
            except OverflowError:
                is_finite_min_p = False
            if not is_finite_min_p or not 0.0 <= min_p <= 1.0:
                raise ValueError(
                    "min_p must be finite and between 0.0 and 1.0"
                )
        if repeat_penalty is not None:
            if isinstance(repeat_penalty, bool) or not isinstance(
                repeat_penalty,
                (int, float),
            ):
                raise TypeError("repeat_penalty must be numeric")
            try:
                is_finite_repeat_penalty = math.isfinite(repeat_penalty)
            except OverflowError:
                is_finite_repeat_penalty = False
            if (
                not is_finite_repeat_penalty
                or not 0.5 <= repeat_penalty <= 2.0
            ):
                raise ValueError(
                    "repeat_penalty must be finite and between 0.5 and 2.0"
                )
        if repeat_last_n is not None:
            if isinstance(repeat_last_n, bool) or not isinstance(
                repeat_last_n,
                int,
            ):
                raise TypeError("repeat_last_n must be an integer")
            if not 0 <= repeat_last_n <= 2_048:
                raise ValueError("repeat_last_n must be between 0 and 2048")

        runtime = self._runtimes.get(model.descriptor.runtime_id)
        if runtime is None:
            raise TextGenerationRuntimeUnsupportedError(
                "no text-generation adapter is registered for this model"
            )
        return await runtime.generate_text(
            model.runtime_reference,
            messages,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            seed=seed,
            top_p=top_p,
            top_k=top_k,
            min_p=min_p,
            repeat_penalty=repeat_penalty,
            repeat_last_n=repeat_last_n,
        )
