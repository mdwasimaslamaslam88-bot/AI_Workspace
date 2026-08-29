from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import os
import platform
import shutil
import subprocess


GIBIBYTE = 1024**3
DEFAULT_GPU_RESERVE_BYTES = 1536 * 1024**2
DEFAULT_RAM_RESERVE_BYTES = 8 * GIBIBYTE
HARDWARE_DETECTION_TIMEOUT_SECONDS = 2.0
HARDWARE_PROFILE_TIERS_GIB = (
    12,
    16,
    24,
    32,
    48,
    64,
    80,
    96,
    128,
    256,
    512,
    1_024,
)


class HardwareClass(StrEnum):
    CPU_ONLY = "cpu_only"
    GPU_UNDER_8GB = "gpu_under_8gb"
    GPU_8_TO_15GB = "gpu_8_to_15gb"
    GPU_16_TO_23GB = "gpu_16_to_23gb"
    GPU_24_TO_47GB = "gpu_24_to_47gb"
    GPU_48_TO_79GB = "gpu_48_to_79gb"
    GPU_80GB_PLUS = "gpu_80gb_plus"
    MULTI_GPU = "multi_gpu"


class OffloadPolicy(StrEnum):
    NONE = "none"
    CPU = "cpu"
    TENSOR_PARALLEL = "tensor_parallel"
    CPU_OR_TENSOR_PARALLEL = "cpu_or_tensor_parallel"


class HardwareAdmissionStatus(StrEnum):
    RUNNABLE = "runnable"
    OFFLOAD_REQUIRED = "offload_required"
    INSUFFICIENT_HARDWARE = "insufficient_hardware"
    NOT_INSTALLED = "not_installed"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class HardwareInventory:
    total_ram_bytes: int
    gpu_vram_bytes: tuple[int, ...] = ()
    gpu_names: tuple[str, ...] = ()
    gpu_compute_capabilities: tuple[str, ...] = ()
    available_ram_bytes: int | None = None
    swap_total_bytes: int = 0
    swap_free_bytes: int = 0
    storage_total_bytes: int | None = None
    storage_free_bytes: int | None = None
    cpu_model: str = "Unknown CPU"
    cpu_logical_count: int = 1
    os_name: str = "unknown"
    os_version: str = "unknown"
    architecture: str = "unknown"
    gpu_vendors: tuple[str, ...] = ()
    gpu_free_vram_bytes: tuple[int, ...] = ()
    gpu_driver_versions: tuple[str, ...] = ()
    accelerator_runtime_names: tuple[str, ...] = ()
    accelerator_runtime_versions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            isinstance(self.total_ram_bytes, bool)
            or not isinstance(self.total_ram_bytes, int)
            or self.total_ram_bytes < 1
        ):
            raise ValueError("total RAM must be a positive integer")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in self.gpu_vram_bytes
        ):
            raise ValueError("GPU VRAM values must be positive integers")
        if self.gpu_names and len(self.gpu_names) != len(self.gpu_vram_bytes):
            raise ValueError("GPU names must match detected GPU capacity entries")
        if self.gpu_compute_capabilities and len(
            self.gpu_compute_capabilities
        ) != len(self.gpu_vram_bytes):
            raise ValueError(
                "GPU compute capabilities must match detected GPU capacity entries"
            )
        for field_name, values in (
            ("GPU vendors", self.gpu_vendors),
            ("GPU free VRAM", self.gpu_free_vram_bytes),
            ("GPU driver versions", self.gpu_driver_versions),
            ("accelerator runtime names", self.accelerator_runtime_names),
            ("accelerator runtime versions", self.accelerator_runtime_versions),
        ):
            if values and len(values) != len(self.gpu_vram_bytes):
                raise ValueError(f"{field_name} must match detected GPU entries")
        for field_name, value in (
            ("available RAM", self.available_ram_bytes),
            ("storage total", self.storage_total_bytes),
            ("storage free", self.storage_free_bytes),
        ):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"{field_name} must be a non-negative integer")
        for field_name, value in (
            ("swap total", self.swap_total_bytes),
            ("swap free", self.swap_free_bytes),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        if (
            self.available_ram_bytes is not None
            and self.available_ram_bytes > self.total_ram_bytes
        ):
            raise ValueError("available RAM cannot exceed total RAM")
        if self.swap_free_bytes > self.swap_total_bytes:
            raise ValueError("free swap cannot exceed total swap")
        if (
            self.storage_total_bytes is not None
            and self.storage_free_bytes is not None
            and self.storage_free_bytes > self.storage_total_bytes
        ):
            raise ValueError("free storage cannot exceed total storage")
        if isinstance(self.cpu_logical_count, bool) or not isinstance(
            self.cpu_logical_count, int
        ) or self.cpu_logical_count < 1:
            raise ValueError("CPU logical count must be positive")
        if any(
            not name
            or name != name.strip()
            or len(name) > 96
            or any(ord(character) < 0x20 for character in name)
            for name in self.gpu_names
        ):
            raise ValueError("GPU names must be bounded printable identifiers")
        if any(
            not capability
            or capability != capability.strip()
            or len(capability) > 16
            or any(
                character not in "0123456789."
                for character in capability
            )
            for capability in self.gpu_compute_capabilities
        ):
            raise ValueError("GPU compute capabilities must be bounded numeric labels")
        for field_name, values, maximum in (
            ("GPU vendors", self.gpu_vendors, 32),
            ("GPU driver versions", self.gpu_driver_versions, 32),
            ("accelerator runtime names", self.accelerator_runtime_names, 32),
            ("accelerator runtime versions", self.accelerator_runtime_versions, 32),
        ):
            if any(not _bounded_printable(value, maximum) for value in values):
                raise ValueError(f"{field_name} must be bounded printable identifiers")
        for field_name, value, maximum in (
            ("CPU model", self.cpu_model, 160),
            ("OS name", self.os_name, 64),
            ("OS version", self.os_version, 128),
            ("architecture", self.architecture, 32),
        ):
            if not _bounded_printable(value, maximum):
                raise ValueError(f"{field_name} must be bounded printable metadata")
        if any(
            free > total
            for free, total in zip(self.gpu_free_vram_bytes, self.gpu_vram_bytes)
        ):
            raise ValueError("free GPU VRAM cannot exceed total GPU VRAM")

    @property
    def largest_gpu_vram_bytes(self) -> int:
        return max(self.gpu_vram_bytes, default=0)

    @property
    def aggregate_gpu_vram_bytes(self) -> int:
        return sum(self.gpu_vram_bytes)

    @property
    def hardware_class(self) -> HardwareClass:
        if len(self.gpu_vram_bytes) > 1:
            return HardwareClass.MULTI_GPU
        return hardware_class_for_vram(self.largest_gpu_vram_bytes)

    @property
    def fingerprint(self) -> str:
        """Stable hardware/runtime identity; excludes transient free capacity."""

        payload = {
            "architecture": self.architecture,
            "cpu_logical_count": self.cpu_logical_count,
            "cpu_model": self.cpu_model,
            "gpus": [
                {
                    "compute": _aligned(self.gpu_compute_capabilities, index),
                    "driver": _aligned(self.gpu_driver_versions, index),
                    "model": _aligned(self.gpu_names, index),
                    "runtime": _aligned(self.accelerator_runtime_names, index),
                    "runtime_version": _aligned(
                        self.accelerator_runtime_versions, index
                    ),
                    "vendor": _aligned(self.gpu_vendors, index),
                    "vram_bytes": vram,
                }
                for index, vram in enumerate(self.gpu_vram_bytes)
            ],
            "os_name": self.os_name,
            "os_version": self.os_version,
            "storage_total_bytes": self.storage_total_bytes,
            "swap_total_bytes": self.swap_total_bytes,
            "total_ram_bytes": self.total_ram_bytes,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


def _bounded_printable(value: str, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and len(value) <= maximum
        and all(ord(character) >= 0x20 for character in value)
    )


def _aligned(values: tuple[str, ...], index: int) -> str | None:
    return values[index] if index < len(values) else None


@dataclass(frozen=True, slots=True)
class HardwareProfile:
    profile_gib: int
    gpu_count: int
    total_vram_bytes: int
    usable_vram_bytes: int
    reserved_vram_bytes: int
    total_ram_bytes: int
    cpu_logical_count: int
    offload_capable: bool
    tensor_parallel_capable: bool
    safe_utilization_limit: float
    simulated: bool = False


def hardware_profile(
    inventory: HardwareInventory,
    *,
    simulated: bool = False,
    safe_utilization_limit: float = 0.90,
) -> HardwareProfile:
    if not isinstance(inventory, HardwareInventory):
        raise TypeError("inventory must be a HardwareInventory")
    if not 0.5 <= safe_utilization_limit <= 0.95:
        raise ValueError("safe utilization limit must be between 0.5 and 0.95")
    largest = inventory.largest_gpu_vram_bytes
    largest_gib = largest / GIBIBYTE
    profile_gib = (
        0
        if largest == 0
        else next(
            (tier for tier in HARDWARE_PROFILE_TIERS_GIB if largest_gib <= tier),
            1_024,
        )
    )
    reserves = tuple(
        max(
            DEFAULT_GPU_RESERVE_BYTES,
            int(value * (1.0 - safe_utilization_limit)),
        )
        for value in inventory.gpu_vram_bytes
    )
    return HardwareProfile(
        profile_gib=profile_gib,
        gpu_count=len(inventory.gpu_vram_bytes),
        total_vram_bytes=inventory.aggregate_gpu_vram_bytes,
        usable_vram_bytes=sum(
            max(0, value - reserve)
            for value, reserve in zip(inventory.gpu_vram_bytes, reserves)
        ),
        reserved_vram_bytes=sum(reserves),
        total_ram_bytes=inventory.total_ram_bytes,
        cpu_logical_count=inventory.cpu_logical_count,
        offload_capable=inventory.total_ram_bytes > DEFAULT_RAM_RESERVE_BYTES,
        tensor_parallel_capable=len(inventory.gpu_vram_bytes) > 1,
        safe_utilization_limit=safe_utilization_limit,
        simulated=simulated,
    )


def hardware_class_for_vram(vram_bytes: int) -> HardwareClass:
    if vram_bytes <= 0:
        return HardwareClass.CPU_ONLY
    gibibytes = vram_bytes / GIBIBYTE
    if gibibytes < 8:
        return HardwareClass.GPU_UNDER_8GB
    if gibibytes < 16:
        return HardwareClass.GPU_8_TO_15GB
    if gibibytes < 24:
        return HardwareClass.GPU_16_TO_23GB
    if gibibytes < 48:
        return HardwareClass.GPU_24_TO_47GB
    if gibibytes < 80:
        return HardwareClass.GPU_48_TO_79GB
    return HardwareClass.GPU_80GB_PLUS


def _memory_details() -> tuple[int, int | None, int, int]:
    values: dict[str, int] = {}
    try:
        with open("/proc/meminfo", encoding="utf-8") as source:
            for line in source:
                key, separator, raw = line.partition(":")
                if separator and raw.strip().endswith(" kB"):
                    number = raw.strip()[:-3].strip()
                    if number.isdecimal():
                        values[key] = int(number) * 1024
    except OSError:
        values = {}
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        page_count = os.sysconf("SC_PHYS_PAGES")
        total = page_size * page_count
    except (OSError, ValueError):
        total = 0
    total = values.get("MemTotal", total)
    total = total if total > 0 else GIBIBYTE
    available = values.get("MemAvailable")
    return total, available, values.get("SwapTotal", 0), values.get("SwapFree", 0)


def _nvidia_gpu_details() -> tuple[tuple[str, int, int, str, str], ...]:
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.free,compute_cap,driver_version",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=HARDWARE_DETECTION_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return ()

    values: list[tuple[str, int, int, str, str]] = []
    for line in completed.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 5:
            return ()
        (
            normalized_name,
            normalized_memory,
            normalized_free,
            compute_capability,
            driver,
        ) = parts
        if (
            not normalized_name
            or len(normalized_name) > 96
            or any(ord(character) < 0x20 for character in normalized_name)
            or not normalized_memory.isdecimal()
            or not normalized_free.isdecimal()
            or not compute_capability
            or len(compute_capability) > 16
            or any(
                character not in "0123456789."
                for character in compute_capability
            )
            or not _bounded_printable(driver, 32)
        ):
            return ()
        mebibytes = int(normalized_memory)
        free_mebibytes = int(normalized_free)
        if mebibytes < 1 or free_mebibytes > mebibytes:
            return ()
        values.append(
            (
                normalized_name,
                mebibytes * 1024**2,
                free_mebibytes * 1024**2,
                compute_capability,
                driver,
            )
        )
    return tuple(values)


def _nvidia_cuda_version() -> str:
    try:
        completed = subprocess.run(
            ["nvidia-smi"],
            check=True,
            capture_output=True,
            text=True,
            timeout=HARDWARE_DETECTION_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return "unknown"
    marker = "CUDA Version:"
    for line in completed.stdout.splitlines()[:12]:
        if marker in line:
            candidate = line.split(marker, 1)[1].split("|", 1)[0].strip()
            if _bounded_printable(candidate, 32):
                return candidate
    return "unknown"


def _cpu_model() -> str:
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as source:
            for line in source:
                if line.casefold().startswith("model name"):
                    candidate = line.partition(":")[2].strip()
                    if _bounded_printable(candidate, 160):
                        return candidate
    except OSError:
        pass
    candidate = platform.processor().strip()
    return candidate if _bounded_printable(candidate, 160) else "Unknown CPU"


def detect_hardware() -> HardwareInventory:
    """Detect bounded capacity and a printable GPU model label."""

    gpu_details = _nvidia_gpu_details()
    total_ram, available_ram, swap_total, swap_free = _memory_details()
    try:
        storage = shutil.disk_usage("/")
        storage_total, storage_free = storage.total, storage.free
    except OSError:
        storage_total, storage_free = None, None
    cuda_version = _nvidia_cuda_version() if gpu_details else "unknown"
    gpu_count = len(gpu_details)

    return HardwareInventory(
        total_ram_bytes=total_ram,
        gpu_vram_bytes=tuple(
            vram_bytes
            for _name, vram_bytes, _free, _compute, _driver in gpu_details
        ),
        gpu_names=tuple(
            name for name, _vram, _free, _compute, _driver in gpu_details
        ),
        gpu_compute_capabilities=tuple(
            compute for _name, _vram, _free, compute, _driver in gpu_details
        ),
        available_ram_bytes=available_ram,
        swap_total_bytes=swap_total,
        swap_free_bytes=swap_free,
        storage_total_bytes=storage_total,
        storage_free_bytes=storage_free,
        cpu_model=_cpu_model(),
        cpu_logical_count=os.cpu_count() or 1,
        os_name=platform.system() or "unknown",
        os_version=platform.release() or "unknown",
        architecture=platform.machine() or "unknown",
        gpu_vendors=("NVIDIA",) * gpu_count,
        gpu_free_vram_bytes=tuple(
            free for _name, _vram, free, _compute, _driver in gpu_details
        ),
        gpu_driver_versions=tuple(
            driver for _name, _vram, _free, _compute, driver in gpu_details
        ),
        accelerator_runtime_names=("CUDA",) * gpu_count,
        accelerator_runtime_versions=(cuda_version,) * gpu_count,
    )


@dataclass(frozen=True, slots=True)
class HardwareAdmission:
    status: HardwareAdmissionStatus
    usable_vram_bytes: int
    usable_ram_bytes: int
    required_vram_bytes: int | None
    minimum_vram_bytes: int | None
    required_ram_bytes: int | None
    offload_required_ram_bytes: int | None
    offload_policy: OffloadPolicy

    @property
    def runnable_now(self) -> bool:
        return self.status is HardwareAdmissionStatus.RUNNABLE


@dataclass(frozen=True, slots=True)
class HardwarePlanner:
    inventory: HardwareInventory
    gpu_reserve_bytes: int = DEFAULT_GPU_RESERVE_BYTES
    ram_reserve_bytes: int = DEFAULT_RAM_RESERVE_BYTES

    def __post_init__(self) -> None:
        for name, value in (
            ("GPU reserve", self.gpu_reserve_bytes),
            ("RAM reserve", self.ram_reserve_bytes),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")

    def runnable_now(
        self,
        *,
        installed: bool,
        required_vram_bytes: int | None,
        required_ram_bytes: int | None,
        supports_multi_gpu: bool = False,
    ) -> bool:
        if not installed or required_ram_bytes is None:
            return False
        usable_ram = max(0, self.inventory.total_ram_bytes - self.ram_reserve_bytes)
        if required_ram_bytes > usable_ram:
            return False
        if required_vram_bytes is None:
            return False
        usable_vram = max(
            0,
            self.inventory.largest_gpu_vram_bytes - self.gpu_reserve_bytes,
        )
        if required_vram_bytes <= usable_vram:
            return True
        if not supports_multi_gpu or len(self.inventory.gpu_vram_bytes) < 2:
            return False
        aggregate_usable_vram = sum(
            max(0, value - self.gpu_reserve_bytes)
            for value in self.inventory.gpu_vram_bytes
        )
        return required_vram_bytes <= aggregate_usable_vram

    def admit(
        self,
        *,
        installed: bool,
        available: bool,
        required_vram_bytes: int | None,
        required_ram_bytes: int | None,
        offload_required_ram_bytes: int | None = None,
        minimum_vram_bytes: int | None = None,
        offload_policy: OffloadPolicy = OffloadPolicy.NONE,
        supports_multi_gpu: bool = False,
    ) -> HardwareAdmission:
        if not isinstance(installed, bool):
            raise TypeError("model installation state must be a boolean")
        if not isinstance(available, bool):
            raise TypeError("model availability must be a boolean")
        if not isinstance(offload_policy, OffloadPolicy):
            raise TypeError("offload_policy must be an OffloadPolicy")
        if minimum_vram_bytes is not None and (
            isinstance(minimum_vram_bytes, bool)
            or not isinstance(minimum_vram_bytes, int)
            or minimum_vram_bytes < 0
        ):
            raise ValueError("minimum VRAM must be a non-negative integer")
        for field_name, value in (
            ("required VRAM", required_vram_bytes),
            ("required RAM", required_ram_bytes),
            ("offload required RAM", offload_required_ram_bytes),
        ):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise ValueError(f"{field_name} must be a non-negative integer")

        usable_ram = max(0, self.inventory.total_ram_bytes - self.ram_reserve_bytes)
        largest_usable_vram = max(
            0,
            self.inventory.largest_gpu_vram_bytes - self.gpu_reserve_bytes,
        )
        aggregate_usable_vram = sum(
            max(0, value - self.gpu_reserve_bytes)
            for value in self.inventory.gpu_vram_bytes
        )
        usable_vram = (
            aggregate_usable_vram
            if supports_multi_gpu and len(self.inventory.gpu_vram_bytes) > 1
            else largest_usable_vram
        )

        def result(status: HardwareAdmissionStatus) -> HardwareAdmission:
            return HardwareAdmission(
                status=status,
                usable_vram_bytes=usable_vram,
                usable_ram_bytes=usable_ram,
                required_vram_bytes=required_vram_bytes,
                minimum_vram_bytes=minimum_vram_bytes,
                required_ram_bytes=required_ram_bytes,
                offload_required_ram_bytes=offload_required_ram_bytes,
                offload_policy=offload_policy,
            )

        if not installed:
            return result(HardwareAdmissionStatus.NOT_INSTALLED)
        if not available:
            return result(HardwareAdmissionStatus.UNAVAILABLE)
        if required_vram_bytes is None or required_ram_bytes is None:
            return result(HardwareAdmissionStatus.INSUFFICIENT_HARDWARE)
        if required_vram_bytes <= usable_vram:
            return result(
                HardwareAdmissionStatus.RUNNABLE
                if required_ram_bytes <= usable_ram
                else HardwareAdmissionStatus.INSUFFICIENT_HARDWARE
            )

        cpu_offload_allowed = offload_policy in {
            OffloadPolicy.CPU,
            OffloadPolicy.CPU_OR_TENSOR_PARALLEL,
        }
        required_minimum = (
            required_vram_bytes
            if minimum_vram_bytes is None
            else minimum_vram_bytes
        )
        offload_ram = (
            required_ram_bytes
            if offload_required_ram_bytes is None
            else offload_required_ram_bytes
        )
        if (
            cpu_offload_allowed
            and required_minimum <= largest_usable_vram
            and offload_ram <= usable_ram
        ):
            return result(HardwareAdmissionStatus.OFFLOAD_REQUIRED)
        return result(HardwareAdmissionStatus.INSUFFICIENT_HARDWARE)

    def required_hardware_class(
        self,
        required_vram_bytes: int | None,
        *,
        supports_multi_gpu: bool = False,
    ) -> HardwareClass | None:
        if required_vram_bytes is None:
            return None
        largest_usable = max(
            0,
            self.inventory.largest_gpu_vram_bytes - self.gpu_reserve_bytes,
        )
        if (
            supports_multi_gpu
            and len(self.inventory.gpu_vram_bytes) > 1
            and required_vram_bytes > largest_usable
        ):
            return HardwareClass.MULTI_GPU
        return hardware_class_for_vram(required_vram_bytes)
