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
        )
