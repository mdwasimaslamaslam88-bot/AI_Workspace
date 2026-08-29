# GPU upgrade

The supported startup flow is:

```text
detect hardware
→ compare stable fingerprint
→ invalidate stale capability assumptions
→ rebuild the live model catalog
→ recalculate admission
→ recalculate routes
→ validate runtime/model availability on use
→ activate admitted models or retain explicit fallbacks
```

The fingerprint covers stable CPU, RAM, storage, OS/architecture, GPU vendor/model,
VRAM, compute capability, driver, and accelerator runtime data. It deliberately
excludes transient free VRAM/RAM/storage so normal workload changes do not look
like hardware replacements. The fingerprint contains no credentials or paths and
is persisted as a mode-0600 state file configured by `HARDWARE_STATE_PATH`.

An authenticated `POST /api/v1/diagnostics/hardware/refresh` performs a safe
explicit refresh. If live hardware changed, current allocations and routes are not
hot-swapped. Diagnostics report `upgrade_detected`, cache invalidation, and
`restart_required`; the next normal backend startup performs admission and runtime
validation against the new device. This is the safe hotplug behavior for runtimes
that cannot guarantee live device migration.

Authenticated diagnostics expose bounded hardware, current eligible/blocked
models and reasons, routes/fallbacks, resource capacity, runtime health, upgrade,
runtime-validation, and restart state. They never expose runtime URLs, filesystem paths,
credentials, model outputs, private content, or provider references.

## Acceptance behavior

Automated tests save a current fingerprint, introduce a simulated upgraded
profile, verify newly eligible models and updated routing, retain old fallbacks,
then instantiate the original profile again. API, UI, database, security, and
benchmark contracts remain unchanged. Single-GPU tiers and 2/4/8-GPU layouts are
simulation-only and make no execution claim.

Manual actions may still be required to install a trusted model, verify its file,
install a compatible driver/runtime, add a new vendor or inference-server adapter,
or supply runtime-specific parallel configuration. Those are compatibility inputs,
not an application architecture redesign.

The accurate guarantee is:

> WORK STATION automatically detects hardware changes, recalculates model
> eligibility, updates routing and activates supported models without requiring a
> software architecture redesign.

This does not mean every future GPU can run every future model.
