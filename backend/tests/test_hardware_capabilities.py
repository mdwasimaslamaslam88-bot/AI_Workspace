import json

from app.hardware import (
    GIBIBYTE,
    HardwareCapabilityService,
    HardwareInventory,
    hardware_profile,
)


def _inventory(vram_gib: int, *, free_gib: int | None = None) -> HardwareInventory:
    return HardwareInventory(
        total_ram_bytes=80 * GIBIBYTE,
        gpu_vram_bytes=(vram_gib * GIBIBYTE,),
        gpu_names=("Test accelerator",),
        gpu_compute_capabilities=("8.6",),
        available_ram_bytes=70 * GIBIBYTE,
        cpu_model="Test CPU",
        cpu_logical_count=16,
        os_name="Linux",
        os_version="test",
        architecture="x86_64",
        gpu_vendors=("NVIDIA",),
        gpu_free_vram_bytes=(
            ((free_gib if free_gib is not None else vram_gib) * GIBIBYTE),
        ),
        gpu_driver_versions=("580.1",),
        accelerator_runtime_names=("CUDA",),
        accelerator_runtime_versions=("13.0",),
    )


def test_hardware_fingerprint_ignores_transient_free_capacity():
    assert _inventory(12, free_gib=11).fingerprint == _inventory(
        12,
        free_gib=3,
    ).fingerprint
    assert _inventory(12).fingerprint != _inventory(24).fingerprint


def test_hardware_profile_normalizes_current_and_multi_gpu_capacity():
    current = hardware_profile(_inventory(12))
    future = hardware_profile(
        HardwareInventory(
            total_ram_bytes=512 * GIBIBYTE,
            gpu_vram_bytes=(80 * GIBIBYTE, 80 * GIBIBYTE),
            cpu_model="Future CPU",
            cpu_logical_count=64,
        ),
        simulated=True,
    )

    assert current.profile_gib == 12
    assert current.gpu_count == 1
    assert future.profile_gib == 80
    assert future.gpu_count == 2
    assert future.tensor_parallel_capable
    assert future.simulated
    assert hardware_profile(
        HardwareInventory(total_ram_bytes=16 * GIBIBYTE)
    ).profile_gib == 0


def test_hardware_change_is_fail_safe_until_next_startup(tmp_path):
    path = tmp_path / "hardware-state.json"
    current = _inventory(12)
    upgraded = _inventory(48)
    service = HardwareCapabilityService(path, detector=lambda: current)

    initial = service.startup()
    service.confirm_active()
    service.detector = lambda: upgraded
    pending = service.refresh()

    assert not initial.upgrade_detected
    assert pending.inventory == current
    assert pending.pending_fingerprint == upgraded.fingerprint
    assert pending.restart_required
    assert json.loads(path.read_text())["fingerprint"] == current.fingerprint

    restarted = HardwareCapabilityService(path, detector=lambda: upgraded).startup()
    assert restarted.inventory == upgraded
    assert restarted.upgrade_detected
    assert restarted.cache_invalidated
    assert not restarted.restart_required
