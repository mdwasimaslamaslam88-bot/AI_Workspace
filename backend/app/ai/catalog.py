from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import math
import re
from typing import Protocol, runtime_checkable

from app.core.config import MAX_MODEL_LIST_DISCOVERY_SECONDS
from app.hardware.planner import HardwareClass, HardwareInventory, HardwarePlanner


_RUNTIME_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_PUBLIC_MODEL_ID_PATTERN = re.compile(
    r"^[a-z0-9][a-z0-9_-]{0,63}:[a-f0-9]{24}$"
)
_PUBLIC_METADATA_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ +()-]{0,254}$")


class ModelModality(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    MULTIMODAL = "multimodal"


class ModelCapability(StrEnum):
    TEXT_GENERATION = "text_generation"
    CHAT = "chat"
    CODE = "code"
    STREAMING = "streaming"
    TOOL_CALLING = "tool_calling"
    STRUCTURED_OUTPUT = "structured_output"
    VISION_INPUT = "vision_input"
    EMBEDDINGS = "embeddings"
    IMAGE_GENERATION = "image_generation"
    IMAGE_EDITING = "image_editing"
    SPEECH_RECOGNITION = "speech_recognition"
    SPEECH_SYNTHESIS = "speech_synthesis"


class ModelScaleClass(StrEnum):
    SEVEN_TO_EIGHT_B = "7b_8b"
    FOURTEEN_B = "14b"
    THIRTY_TO_THIRTY_FOUR_B = "30b_34b"
    SEVENTY_B = "70b"
    HUNDRED_B_PLUS = "100b_plus"
    TWO_HUNDRED_B_PLUS = "200b_plus"
    FIVE_HUNDRED_B_PLUS = "500b_plus"
    ONE_THOUSAND_B_PLUS = "1000b_plus"
    TWO_THOUSAND_B = "2000b"
    MOE_VERY_LARGE = "moe_very_large"


class ModelAvailability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class ModelRuntimeUnavailableError(RuntimeError):
    """A configured local runtime could not provide a safe model inventory."""


def _optional_text(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string or None")
    if not _PUBLIC_METADATA_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} must contain only safe public metadata")
    return value


def normalize_capabilities(
    values: tuple[ModelCapability | str, ...],
) -> tuple[ModelCapability, ...]:
    normalized: set[ModelCapability] = set()
    for value in values:
        if isinstance(value, ModelCapability):
            normalized.add(value)
            continue
        if not isinstance(value, str):
            raise TypeError("model capabilities must be strings")
        candidate = value.strip().lower().replace("-", "_").replace(" ", "_")
        try:
            normalized.add(ModelCapability(candidate))
        except ValueError:
            continue
    return tuple(sorted(normalized, key=lambda capability: capability.value))


@dataclass(frozen=True, slots=True)
class RuntimeModel:
    """Provider result retaining its opaque reference below the API boundary."""

    reference: str
    display_name: str
    modality: ModelModality = ModelModality.TEXT
    family: str | None = None
    parameter_class: str | None = None
    capabilities: tuple[ModelCapability | str, ...] = ()
    context_window: int | None = None
    quantization: str | None = None
    estimated_vram_bytes: int | None = None
    availability: ModelAvailability = ModelAvailability.AVAILABLE
    scale_class: ModelScaleClass | None = None
    required_vram_bytes: int | None = None
    required_ram_bytes: int | None = None
    installed: bool = True
    supports_multi_gpu: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.reference, str):
            raise TypeError("runtime model reference must be a string")
        if not self.reference.strip():
            raise ValueError("runtime model reference must not be blank")
        if not isinstance(self.display_name, str):
            raise TypeError("model display_name must be a string")
        if not _PUBLIC_METADATA_PATTERN.fullmatch(self.display_name):
            raise ValueError(
                "model display_name must contain only safe public metadata"
            )
        if not isinstance(self.modality, ModelModality):
            raise TypeError("model modality must be a ModelModality")
        if not isinstance(self.availability, ModelAvailability):
            raise TypeError("model availability must be a ModelAvailability")
        if self.scale_class is not None and not isinstance(
            self.scale_class, ModelScaleClass
        ):
            raise TypeError("model scale_class must be a ModelScaleClass or None")
        if not isinstance(self.installed, bool):
            raise TypeError("model installed must be a boolean")
        if not isinstance(self.supports_multi_gpu, bool):
            raise TypeError("model supports_multi_gpu must be a boolean")
        _optional_text(self.family, "model family")
        _optional_text(self.parameter_class, "model parameter_class")
        _optional_text(self.quantization, "model quantization")
        if self.context_window is not None and (
            isinstance(self.context_window, bool)
            or not isinstance(self.context_window, int)
            or self.context_window < 1
        ):
            raise ValueError("model context_window must be a positive integer")
        if self.estimated_vram_bytes is not None and (
            isinstance(self.estimated_vram_bytes, bool)
            or not isinstance(self.estimated_vram_bytes, int)
            or self.estimated_vram_bytes < 0
        ):
            raise ValueError(
                "model estimated_vram_bytes must be a non-negative integer"
            )
        for field_name, value in (
            ("required_vram_bytes", self.required_vram_bytes),
            ("required_ram_bytes", self.required_ram_bytes),
        ):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise ValueError(
                    f"model {field_name} must be a non-negative integer"
                )
        object.__setattr__(
            self,
            "capabilities",
            normalize_capabilities(self.capabilities),
        )


@dataclass(frozen=True, slots=True)
class ModelDescriptor:
    model_id: str
    display_name: str
    runtime_id: str
    modality: ModelModality
    family: str | None
    parameter_class: str | None
    capabilities: tuple[ModelCapability, ...]
    context_window: int | None
    quantization: str | None
    estimated_vram_bytes: int | None
    availability: ModelAvailability
    scale_class: ModelScaleClass | None = None
    required_vram_bytes: int | None = None
    required_ram_bytes: int | None = None
    installed: bool = True
    runnable_now: bool = True
    future_capable: bool = False
    hardware_class: HardwareClass | None = None
    fallback_model_id: str | None = None
    supports_multi_gpu: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.model_id, str) or not _PUBLIC_MODEL_ID_PATTERN.fullmatch(
            self.model_id
        ):
            raise ValueError("model_id must be a runtime-namespaced public ID")
        if not isinstance(self.runtime_id, str) or not _RUNTIME_ID_PATTERN.fullmatch(
            self.runtime_id
        ):
            raise ValueError("runtime_id must be a normalized runtime identifier")
        if not self.model_id.startswith(f"{self.runtime_id}:"):
            raise ValueError("model_id namespace must match runtime_id")
        validated = RuntimeModel(
            reference="internal-validation-only",
            display_name=self.display_name,
            modality=self.modality,
            family=self.family,
            parameter_class=self.parameter_class,
            capabilities=self.capabilities,
            context_window=self.context_window,
            quantization=self.quantization,
            estimated_vram_bytes=self.estimated_vram_bytes,
            availability=self.availability,
            scale_class=self.scale_class,
            required_vram_bytes=self.required_vram_bytes,
            required_ram_bytes=self.required_ram_bytes,
            installed=self.installed,
            supports_multi_gpu=self.supports_multi_gpu,
        )
        if not isinstance(self.runnable_now, bool):
            raise TypeError("model runnable_now must be a boolean")
        if not isinstance(self.future_capable, bool):
            raise TypeError("model future_capable must be a boolean")
        if self.hardware_class is not None and not isinstance(
            self.hardware_class, HardwareClass
        ):
            raise TypeError("model hardware_class must be a HardwareClass or None")
        if self.fallback_model_id is not None and not _PUBLIC_MODEL_ID_PATTERN.fullmatch(
            self.fallback_model_id
        ):
            raise ValueError("model fallback_model_id must be a public model ID or None")
        object.__setattr__(self, "capabilities", validated.capabilities)


@dataclass(frozen=True, slots=True)
class ResolvedModel:
    """Internal catalog binding that must never cross the API boundary."""

    descriptor: ModelDescriptor
    runtime_reference: str

    def __post_init__(self) -> None:
        if not isinstance(self.descriptor, ModelDescriptor):
            raise TypeError("resolved model descriptor must be a ModelDescriptor")
        if not isinstance(self.runtime_reference, str):
            raise TypeError("resolved model runtime_reference must be a string")
        if not self.runtime_reference.strip():
            raise ValueError("resolved model runtime_reference must not be blank")


@runtime_checkable
class ModelDiscoveryRuntime(Protocol):
    runtime_id: str
    supports_reference_selector: bool

    async def discover_models(
        self,
        *,
        reference_selector: Callable[[str], bool] | None = None,
    ) -> tuple[RuntimeModel, ...]: ...


@dataclass(slots=True)
class _ListModelsFlight:
    task: asyncio.Task[tuple[ModelDescriptor, ...]]
    waiter_count: int = 0


class ModelCatalog:
    def __init__(
        self,
        runtimes: tuple[ModelDiscoveryRuntime, ...] = (),
        *,
        max_list_discovery_seconds: float = 60.0,
        hardware_inventory: HardwareInventory | None = None,
    ) -> None:
        if isinstance(max_list_discovery_seconds, bool) or not isinstance(
            max_list_discovery_seconds,
            (int, float),
        ):
            raise TypeError("model list discovery deadline must be numeric")
        try:
            is_finite = math.isfinite(max_list_discovery_seconds)
        except OverflowError:
            is_finite = False
        if (
            not is_finite
            or not 0 < max_list_discovery_seconds
            <= MAX_MODEL_LIST_DISCOVERY_SECONDS
        ):
            raise ValueError(
                "model list discovery deadline must be positive and no greater "
                f"than {MAX_MODEL_LIST_DISCOVERY_SECONDS} seconds"
            )
        runtime_ids: set[str] = set()
        for runtime in runtimes:
            runtime_id = runtime.runtime_id
            if not isinstance(runtime_id, str) or not _RUNTIME_ID_PATTERN.fullmatch(
                runtime_id
            ):
                raise ValueError("runtime_id must be a normalized runtime identifier")
            if runtime_id in runtime_ids:
                raise ValueError(f"duplicate runtime_id: {runtime_id}")
            runtime_ids.add(runtime_id)
        self.runtimes = runtimes
        self.hardware_planner = (
            HardwarePlanner(hardware_inventory)
            if hardware_inventory is not None
            else None
        )
        self.max_list_discovery_seconds = float(
            max_list_discovery_seconds
        )
        self._list_models_flight: _ListModelsFlight | None = None
        self._list_models_flight_lock = asyncio.Lock()

    async def list_models(self) -> tuple[ModelDescriptor, ...]:
        flight = await self._join_list_models_flight()
        try:
            return await asyncio.shield(flight.task)
        finally:
            await self._leave_list_models_flight(flight)

    async def _list_models_uncached(self) -> tuple[ModelDescriptor, ...]:
        resolved: dict[str, ResolvedModel] = {}
        for runtime in self.runtimes:
            for model in await self._discover_runtime(runtime):
                model_id = model.descriptor.model_id
                if model_id in resolved:
                    raise ValueError(f"duplicate public model_id: {model_id}")
                resolved[model_id] = model
        ordered = tuple(
            sorted(
                (model.descriptor for model in resolved.values()),
                key=lambda item: (item.display_name.casefold(), item.model_id),
            )
        )
        return self._attach_fallbacks(ordered)

    @staticmethod
    def _attach_fallbacks(
        models: tuple[ModelDescriptor, ...],
    ) -> tuple[ModelDescriptor, ...]:
        from dataclasses import replace

        runnable_text = sorted(
            (
                model
                for model in models
                if model.runnable_now
                and ModelCapability.TEXT_GENERATION in model.capabilities
            ),
            key=lambda model: (
                model.required_vram_bytes is None,
                model.required_vram_bytes or 0,
                model.model_id,
            ),
        )
        if not runnable_text:
            return models
        fallback = runnable_text[0]
        return tuple(
            replace(model, fallback_model_id=fallback.model_id)
            if not model.runnable_now
            and ModelCapability.TEXT_GENERATION in model.capabilities
            and model.model_id != fallback.model_id
            else model
            for model in models
        )

    async def _join_list_models_flight(self) -> _ListModelsFlight:
        while True:
            task_to_wait: asyncio.Task[tuple[ModelDescriptor, ...]] | None = None
            async with self._list_models_flight_lock:
                flight = self._list_models_flight
                if (
                    flight is not None
                    and flight.waiter_count == 0
                    and not flight.task.done()
                ):
                    task_to_wait = flight.task
                else:
                    if flight is None or flight.task.done():
                        task = asyncio.create_task(
                            self._run_list_models_flight(),
                            name="model-catalog-list-models-discovery",
                        )
                        flight = _ListModelsFlight(task=task)
                        self._list_models_flight = flight
                    flight.waiter_count += 1
                    return flight

            if task_to_wait is not None:
                await self._wait_for_retiring_list_models_flight(task_to_wait)

    @staticmethod
    async def _wait_for_retiring_list_models_flight(
        task: asyncio.Task[tuple[ModelDescriptor, ...]],
    ) -> None:
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            current_task = asyncio.current_task()
            if current_task is not None and current_task.cancelling():
                raise
        except BaseException:
            pass

    async def _run_list_models_flight(self) -> tuple[ModelDescriptor, ...]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.max_list_discovery_seconds
        deadline_scope = asyncio.timeout_at(deadline)
        try:
            try:
                async with deadline_scope:
                    result = await self._list_models_uncached()
                    if loop.time() >= deadline:
                        raise ModelRuntimeUnavailableError(
                            "local model discovery is unavailable"
                        )
                    return result
            except TimeoutError as exc:
                if not deadline_scope.expired():
                    raise
                raise ModelRuntimeUnavailableError(
                    "local model discovery is unavailable"
                ) from exc
        finally:
            current_task = asyncio.current_task()
            async with self._list_models_flight_lock:
                flight = self._list_models_flight
                if flight is not None and flight.task is current_task:
                    self._list_models_flight = None

    async def _leave_list_models_flight(
        self,
        flight: _ListModelsFlight,
    ) -> None:
        task_to_stop: asyncio.Task[tuple[ModelDescriptor, ...]] | None = None
        async with self._list_models_flight_lock:
            flight.waiter_count -= 1
            if flight.waiter_count == 0 and not flight.task.done():
                flight.task.cancel()
                task_to_stop = flight.task

        if task_to_stop is not None:
            await self._await_task_termination(task_to_stop)
            async with self._list_models_flight_lock:
                if self._list_models_flight is flight:
                    self._list_models_flight = None

    @staticmethod
    async def _await_task_termination(
        task: asyncio.Task[tuple[ModelDescriptor, ...]],
    ) -> None:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        if not task.cancelled():
            task.exception()

    async def resolve_model(self, model_id: str) -> ResolvedModel | None:
        if not isinstance(model_id, str) or not _PUBLIC_MODEL_ID_PATTERN.fullmatch(
            model_id
        ):
            raise ValueError("model_id must be a runtime-namespaced public ID")

        runtime_id = model_id.split(":", 1)[0]
        runtime = next(
            (
                candidate
                for candidate in self.runtimes
                if candidate.runtime_id == runtime_id
            ),
            None,
        )
        if runtime is None:
            return None

        resolved: ResolvedModel | None = None
        for model in await self._discover_runtime(
            runtime,
            reference_selector=lambda reference: (
                _public_model_id(runtime.runtime_id, reference) == model_id
            ),
        ):
            if model.descriptor.model_id != model_id:
                continue
            if resolved is not None:
                raise ValueError(f"duplicate public model_id: {model_id}")
            resolved = model
        return resolved

    async def _discover_runtime(
        self,
        runtime: ModelDiscoveryRuntime,
        *,
        reference_selector: Callable[[str], bool] | None = None,
    ) -> tuple[ResolvedModel, ...]:
        resolved: list[ResolvedModel] = []
        public_ids: set[str] = set()
        discovered = (
            await runtime.discover_models()
            if (
                reference_selector is None
                or getattr(
                    runtime,
                    "supports_reference_selector",
                    False,
                ) is not True
            )
            else await runtime.discover_models(
                reference_selector=reference_selector
            )
        )
        for model in discovered:
            if not isinstance(model, RuntimeModel):
                raise TypeError("runtime discovery must return RuntimeModel values")
            model_id = _public_model_id(runtime.runtime_id, model.reference)
            if model_id in public_ids:
                raise ValueError(f"duplicate public model_id: {model_id}")
            public_ids.add(model_id)
            runnable_now = (
                self.hardware_planner.runnable_now(
                    installed=model.installed,
                    required_vram_bytes=model.required_vram_bytes,
                    required_ram_bytes=model.required_ram_bytes,
                    supports_multi_gpu=model.supports_multi_gpu,
                )
                if self.hardware_planner is not None
                else (
                    model.installed
                    and model.availability is ModelAvailability.AVAILABLE
                )
            )
            future_capable = (
                not runnable_now
                and model.availability is ModelAvailability.AVAILABLE
                and model.required_vram_bytes is not None
                and model.required_ram_bytes is not None
            )
            resolved.append(
                ResolvedModel(
                    descriptor=ModelDescriptor(
                        model_id=model_id,
                        display_name=model.display_name,
                        runtime_id=runtime.runtime_id,
                        modality=model.modality,
                        family=model.family,
                        parameter_class=model.parameter_class,
                        capabilities=model.capabilities,
                        context_window=model.context_window,
                        quantization=model.quantization,
                        estimated_vram_bytes=model.estimated_vram_bytes,
                        availability=model.availability,
                        scale_class=model.scale_class,
                        required_vram_bytes=model.required_vram_bytes,
                        required_ram_bytes=model.required_ram_bytes,
                        installed=model.installed,
                        runnable_now=runnable_now,
                        future_capable=future_capable,
                        supports_multi_gpu=model.supports_multi_gpu,
                        hardware_class=(
                            self.hardware_planner.required_hardware_class(
                                model.required_vram_bytes,
                                supports_multi_gpu=model.supports_multi_gpu,
                            )
                            if self.hardware_planner is not None
                            else None
                        ),
                    ),
                    runtime_reference=model.reference,
                )
            )
        return tuple(resolved)


def _public_model_id(runtime_id: str, runtime_reference: str) -> str:
    digest = hashlib.sha256(
        f"{runtime_id}\0{runtime_reference}".encode("utf-8")
    ).hexdigest()[:24]
    return f"{runtime_id}:{digest}"
