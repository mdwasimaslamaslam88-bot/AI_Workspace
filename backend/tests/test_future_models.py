from app.ai.future_models import (
    FUTURE_MODEL_CONTRACTS,
    HARDWARE_SIMULATION_TIERS_GIB,
    hardware_admission_matrix,
)
from app.hardware.planner import (
    GIBIBYTE,
    HardwareAdmissionStatus,
    HardwareInventory,
    HardwarePlanner,
)


def _status(matrix, tier, profile):
    tier_record = next(
        item for item in matrix["tiers"] if item["gpu_vram_gib"] == tier
    )
    return next(
        item["status"]
        for item in tier_record["admissions"]
        if item["profile_id"] == profile
    )


def test_future_contract_covers_every_required_scale_without_execution_claims():
    scale_classes = {profile.scale_class.value for profile in FUTURE_MODEL_CONTRACTS}

    assert {
        "7b_8b",
        "14b",
        "30b_34b",
        "70b",
        "100b_plus",
        "200b_plus",
        "500b_plus",
        "1000b_plus",
        "2000b",
        "moe_very_large",
    } <= scale_classes
    assert all(profile.required_vram_bytes > 0 for profile in FUTURE_MODEL_CONTRACTS)


def test_hardware_admission_matrix_is_hardware_driven_and_fail_closed():
    matrix = hardware_admission_matrix()

    assert matrix["simulation_only"] is True
    assert matrix["actual_execution_claimed"] is False
    assert tuple(item["gpu_vram_gib"] for item in matrix["tiers"]) == (
        HARDWARE_SIMULATION_TIERS_GIB
    )
    assert _status(matrix, 12, "dense-200b-q4") == "insufficient_hardware"
    assert _status(matrix, 256, "dense-200b-q4") == "runnable"
    assert _status(matrix, 1_024, "dense-2000b-q4") == "offload_required"


def test_full_gpu_200b_profile_needs_no_system_ram_redesign():
    profile = next(
        item for item in FUTURE_MODEL_CONTRACTS
        if item.profile_id == "dense-200b-q4"
    )
    planner = HardwarePlanner(
        HardwareInventory(
            total_ram_bytes=80 * GIBIBYTE,
            gpu_vram_bytes=(256 * GIBIBYTE,),
            gpu_names=("Future accelerator",),
        )
    )

    admission = planner.admit(
        installed=True,
        available=True,
        required_vram_bytes=profile.required_vram_bytes,
        minimum_vram_bytes=profile.minimum_vram_bytes,
        required_ram_bytes=profile.required_ram_bytes,
        offload_required_ram_bytes=profile.offload_required_ram_bytes,
        offload_policy=profile.offload_policy,
        supports_multi_gpu=profile.tensor_parallel_gpu_count > 1,
    )

    assert admission.status is HardwareAdmissionStatus.RUNNABLE
