#!/usr/bin/env python3
from __future__ import annotations

import json

from app.ai.admission import (
    ModelAdmissionEngine,
    ModelAdmissionRequest,
    ModelEligibilityStatus,
)
from app.ai.future_models import (
    HARDWARE_SIMULATION_TIERS_GIB,
    hardware_admission_matrix,
)
from app.hardware import GIBIBYTE, HardwareInventory, detect_hardware, hardware_profile


def _simulated(vram_gib: int, ram_gib: int) -> HardwareInventory:
    return HardwareInventory(
        total_ram_bytes=ram_gib * GIBIBYTE,
        gpu_vram_bytes=(vram_gib * GIBIBYTE,),
        gpu_names=("Simulation-only accelerator",),
        gpu_compute_capabilities=("9.0",),
        gpu_vendors=("simulation",),
        cpu_model="Simulation-only CPU",
        cpu_logical_count=64,
        os_name="simulation",
        os_version="1",
        architecture="x86_64",
    )


def main() -> int:
    current = detect_hardware()
    current_profile = hardware_profile(current)
    matrix = hardware_admission_matrix()
    sample_request = ModelAdmissionRequest(
        installed=True,
        available=True,
        required_vram_bytes=40 * GIBIBYTE,
        required_ram_bytes=48 * GIBIBYTE,
    )
    current_sample = ModelAdmissionEngine(current).evaluate(sample_request)
    upgraded_sample = ModelAdmissionEngine(_simulated(48, 256)).evaluate(
        sample_request
    )

    if tuple(item["gpu_vram_gib"] for item in matrix["tiers"]) != (
        HARDWARE_SIMULATION_TIERS_GIB
    ):
        raise RuntimeError("future hardware tier coverage changed")
    if current_profile.profile_gib <= 12 and (
        current_sample.status is not ModelEligibilityStatus.HARDWARE_INSUFFICIENT
    ):
        raise RuntimeError("current hardware admitted the oversized sample")
    if upgraded_sample.status is not ModelEligibilityStatus.RUNNABLE_NOW:
        raise RuntimeError("simulated upgrade did not admit the sample")

    print(
        json.dumps(
            {
                "actual_hardware": {
                    "fingerprint": current.fingerprint,
                    "gpu_count": current_profile.gpu_count,
                    "profile_gib": current_profile.profile_gib,
                    "hardware_class": current.hardware_class.value,
                },
                "simulation_only": True,
                "actual_execution_claimed_for_simulations": False,
                "current_sample_status": current_sample.status.value,
                "simulated_48gib_sample_status": upgraded_sample.status.value,
                "single_gpu_tiers": list(HARDWARE_SIMULATION_TIERS_GIB),
                "multi_gpu_layout_count": len(matrix["multi_gpu_layouts"]),
                "result": "pass",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
