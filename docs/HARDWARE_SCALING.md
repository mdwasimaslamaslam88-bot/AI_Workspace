# Hardware scaling

WORK STATION treats hardware as discovered capacity, not as a product identity. The
current verified host has one NVIDIA RTX 3060 with 12 GiB VRAM. That fact affects
admission today; it is not embedded in API, database, RAG, memory, conversation,
tool, workflow, or client contracts.

## Capability boundary

`app.hardware.planner.detect_hardware()` is the production discovery boundary. It
normalizes GPU vendor/model/count, total and free VRAM, compute capability, driver
and accelerator runtime, plus CPU, RAM, swap, storage, OS, and architecture into
`HardwareInventory`. Other product modules must not parse `nvidia-smi`.

The normalized `HardwareProfile` exposes a bounded capacity tier, usable/reserved
VRAM, host RAM, CPU count, offload capability, multi-GPU capability, and a safe
utilization limit. Supported simulation tiers are 12, 16, 24, 32, 48, 64, 80,
96, 128, 256, 512, and 1024+ GiB. Simulated profiles are explicitly marked and
never constitute an execution claim.

Current Linux discovery supports NVIDIA telemetry and a CPU-only fallback. A
future AMD, Intel, Apple, or other accelerator needs one detector adapter that
emits `HardwareInventory`; it does not require changes to the product APIs or
business logic.

## Resource policy

Admission reserves 1.5 GiB per GPU and 8 GiB of system RAM. `HardwareProfile`
also reports a 90% safe utilization ceiling. Runtime adapters may apply stricter
limits. Dynamic context, batch, concurrency, streaming, KV-cache, and keep-alive
settings remain runtime policy: they may use only admitted capacity and must fail
closed when metadata is missing.

CPU/RAM offload is not automatically considered usable merely because a model
loads. A model must declare runtime support and an `interactive` or `acceptable`
measured performance class. `slow`, `experimental`, and `unsupported` offload are
blocked from automatic routing.

Multi-GPU metadata represents tensor parallelism, pipeline parallelism, sharding,
and runtime-managed device placement. Aggregate VRAM is used only when the model
and runtime explicitly support multi-GPU. No parallel method is activated solely
from registry metadata.

## Verification

Run the isolated acceptance flow:

```bash
cd backend
PYTHONPATH=. .venv/bin/python scripts/hardware_upgrade_acceptance.py
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_hardware_capabilities.py \
  tests/test_model_admission.py \
  tests/test_future_models.py
```

The full dependency inventory is machine-readable at
`docs/hardware-dependency-map.json`.
