# Model admission

`ModelCatalog` is the canonical live model registry. Every runtime adapter emits
the same `RuntimeModel` contract; the catalog converts it into a public,
runtime-namespaced `ModelDescriptor`. Opaque runtime references never cross the
API boundary.

The record supports identity/family/scale/quantization/runtime, context,
modalities and capabilities, required VRAM/RAM, minimum VRAM and compute,
offload, installation, verification, runtime compatibility, multi-GPU, fallback,
and performance metadata. Future scale templates (7B/8B through 2000B and MoE)
use the same admission engine and become live records only after a compatible
runtime discovers an installed model.

## Authoritative calculation

`ModelAdmissionEngine` evaluates:

```text
hardware inventory
+ runtime compatibility
+ model/quantization/context metadata
+ memory reserves
+ compute capability
+ multi-GPU requirements
+ offload support and measured performance
= one eligibility decision with explicit reasons
```

Statuses are `runnable_now`, `runnable_with_offload`, `future_capable`,
`hardware_insufficient`, `runtime_incompatible`, `not_installed`,
`download_required`, `verification_required`, and `disabled`.

Blockers identify insufficient VRAM/RAM, unsupported runtime/compute, absent or
unverified models, required downloads or multiple GPUs, incomplete metadata,
unacceptable offload performance, disabled policy, or runtime unavailability.
There is no hidden fallback: fallbacks are explicit public model IDs attached by
the catalog.

Unknown capacity or unverified model files fail closed. Allowlisting is necessary
but not sufficient: installation, integrity/verification state, runtime discovery,
hardware admission, capability, and context all still have to pass.

Parameter count is descriptive, never sufficient proof of compatibility. The
quantization, actual installed size, runtime behavior, context/KV policy, and
measured resource use must be recorded before production admission.
