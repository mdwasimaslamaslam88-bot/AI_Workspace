from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import os
import subprocess


GIBIBYTE = 1024**3
DEFAULT_GPU_RESERVE_BYTES = 1536 * 1024**2
DEFAULT_RAM_RESERVE_BYTES = 8 * GIBIBYTE
HARDWARE_DETECTION_TIMEOUT_SECONDS = 2.0


class HardwareClass(StrEnum):
    CPU_ONLY = "cpu_only"
    GPU_UNDER_8GB = "gpu_under_8gb"
    GPU_8_TO_15GB = "gpu_8_to_15gb"
    GPU_16_TO_23GB = "gpu_16_to_23gb"
    GPU_24_TO_47GB = "gpu_24_to_47gb"
    GPU_48_TO_79GB = "gpu_48_to_79gb"
    GPU_80GB_PLUS = "gpu_80gb_plus"
    MULTI_GPU = "multi_gpu"


@dataclass(frozen=True, slots=True)
class HardwareInventory:
    total_ram_bytes: int
    gpu_vram_bytes: tuple[int, ...] = ()
    gpu_names: tuple[str, ...] = ()

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
        if any(
            not name
            or name != name.strip()
            or len(name) > 96
            or any(ord(character) < 0x20 for character in name)
            for name in self.gpu_names
        ):
            raise ValueError("GPU names must be bounded printable identifiers")

    @property
    def largest_gpu_vram_bytes(self) -> int:
        return max(self.gpu_vram_bytes, default=0)

    @property
    def hardware_class(self) -> HardwareClass:
        if len(self.gpu_vram_bytes) > 1:
            return HardwareClass.MULTI_GPU
        return hardware_class_for_vram(self.largest_gpu_vram_bytes)


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


def _total_ram_bytes() -> int:
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        page_count = os.sysconf("SC_PHYS_PAGES")
        total = page_size * page_count
    except (OSError, ValueError):
        total = 0
    return total if total > 0 else GIBIBYTE


def _nvidia_gpu_details() -> tuple[tuple[str, int], ...]:
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=HARDWARE_DETECTION_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return ()

    values: list[tuple[str, int]] = []
    for line in completed.stdout.splitlines():
        name, separator, memory = line.partition(",")
        normalized_name = name.strip()
        normalized_memory = memory.strip()
        if (
            separator != ","
            or not normalized_name
            or len(normalized_name) > 96
            or any(ord(character) < 0x20 for character in normalized_name)
            or not normalized_memory.isdecimal()
        ):
            return ()
        mebibytes = int(normalized_memory)
        if mebibytes < 1:
            return ()
        values.append((normalized_name, mebibytes * 1024**2))
    return tuple(values)


def detect_hardware() -> HardwareInventory:
    """Detect bounded capacity and a printable GPU model label."""

    gpu_details = _nvidia_gpu_details()

    return HardwareInventory(
        total_ram_bytes=_total_ram_bytes(),
        gpu_vram_bytes=tuple(vram_bytes for _name, vram_bytes in gpu_details),
        gpu_names=tuple(name for name, _vram_bytes in gpu_details),
    )


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
