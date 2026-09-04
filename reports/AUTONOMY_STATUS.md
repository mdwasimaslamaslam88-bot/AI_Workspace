# Phase I Autonomy, Monitoring, and Recovery Status

The status labels below distinguish active production mechanisms, isolated
recovery evidence, and owner/provider boundaries. High-impact actions remain
approval-gated.

| Capability | Status | Evidence or exact boundary |
|---|---|---|
| Background missions/workflows | RUNTIME PASS | Workflow execution runs asynchronously with concurrency admission, per-step and wall deadlines, cancellation, terminal error classification, and persisted results; generic Agent OS missions now use PostgreSQL checkpoints and append-only lifecycle audit |
| Agent retry and repair | LOCAL PASS | Bounded automatic attempts plus an owner-triggered retry capped at three, verification before completion, cancellation, deadlines and integrity hashes passed focused tests |
| Owner mission controls | LOCAL PASS | Pause/resume, approval hold, approval-invalidating revision and manual retry are owner-isolated, state-gated, bounded and available through matching web/mobile controls |
| Restart recovery | LOCAL PASS | Queued Agent OS missions resume after initialization; interrupted active attempts recover as paused and require explicit owner resume rather than claiming completion |
| Connector retry/failover | LOCAL PASS | Idempotency-aware retries, 429/5xx handling, timeout, circuit breaker, health state, reconnect, and audit contracts passed; no owner external connector is configured |
| Model monitoring/fallback | RUNTIME PASS | Authenticated production diagnostics returned installed-model admission and task routes; local model health is part of five-minute readiness checks |
| Hardware/resource monitoring | RUNTIME PASS | Authoritative hardware diagnostics reported the admitted GPU and validated runtime; VRAM/RAM/model admission guards passed focused tests without forcing artificial resource exhaustion |
| Agent monitoring | RUNTIME PASS | Authenticated production diagnostics returned owner-isolated active/retained agent state |
| Connector monitoring | RUNTIME PASS | Connector runtime configuration and temporary-owner isolation passed over private HTTPS; zero external connectors are configured to health-probe |
| Backend monitoring | ACTIVE | Five-minute dependency readiness timer is active; PostgreSQL, Redis, and Ollama readiness passed |
| Remote gateway monitoring | ACTIVE | Five-minute private-gateway timer is active; Serve target/Funnel guards and post-restart health passed |
| Backend recovery | RUNTIME PASS | Exact supervised process was terminated; systemd detected failure, started a new PID, and local plus remote readiness recovered |
| Remote-daemon recovery | RUNTIME PASS | Exact userspace Tailscale process was terminated; systemd restarted it and the private Serve gateway recovered |
| Technology watcher | ACTIVE | Daily timer is enabled; immediate real fetch completed with `no_new_candidate`; only fast-forward candidates can enter isolated validation |
| Update release gate | NO CURRENT CANDIDATE | The previously ready commit was older than production and is now failed closed as `candidate_superseded`; the next fast-forward candidate must pass all 17 mandatory gates before owner `UPDATE` appears |
| Automatic post-update rollback | ISOLATED PASS | Failed post-activation health switched the managed release back in tests; current checkout service is not moved to the managed symlink before owner activation |
| Production backup integrity | RUNTIME PASS | Latest private backup passed checksums, PostgreSQL archive parsing, asset-member validation, and permissions checks |
| Disaster-recovery restore | RUNTIME PASS | Latest production backup restored into a disposable loopback PostgreSQL cluster and empty asset directory; expected schema and asset root were verified and removed |
| Daily backup schedule | OWNER ACTION REQUIRED | Timer is installed but disabled because no owner-approved encrypted destination is configured; enabling an unverified plaintext destination would weaken the backup policy |
| Remote alert delivery | EXTERNAL BLOCKED | Failures are recorded locally in systemd/audit state; FCM/APNs/EAS or another authorized notification provider and registered device are required for remote push |
| Dangerous autonomous actions | DISABLED | Production update activation, live broker orders, publishing, billing, MFA/OTP, and other high-impact actions retain their existing approval/provider gates |

## Phase I failure and fix

The first real technology-watcher activation failed before Python execution
with systemd exit `218/CAPABILITIES`. Runtime isolation proved
`ProtectKernelModules=yes` was the incompatible directive for a newly activated
user service on this host. The user manager cannot grant kernel-module
privileges, so removing that ineffective directive from the backup and watcher
templates did not expand their effective authority. All remaining sandbox
controls stay enabled. The installed Tailscale unit was also refreshed from its
already-correct repository template.

Production was separately corrected to `REMOTE_GATEWAY_MODE=tailscale` in the
protected environment after an owner-only rollback copy was created. After the
supervised restart, local API documentation returned 404, authenticated
diagnostics reported remote mode, and the full private remote smoke passed with
exact cleanup.

The watcher also found that its retained `ready` state referenced a commit older
than current production. Reconciliation now distinguishes a genuine advancing
candidate from already-active, superseded, divergent, or invalid state. The
stale candidate is `failed`, both ready flags are false, and no activation
button can be offered for it. Checkpoints and candidate evidence are retained
for audit rather than destructively deleted.

## Phase I evidence

- Persistent Agent OS focused suite: **56 passed**; PostgreSQL integration:
  **55 passed**, head `0022`, latest downgrade/re-upgrade passed, no drift.
- Remote-device harness tests and the production monitoring smoke: **passed**.
- User systemd unit syntax/sandbox compatibility: **passed**.
- Backup verification and disposable restore: **passed**.
- Backend and remote-daemon crash recovery: **passed**.
- Security gate: **passed** with no critical/high finding or secret leak. The
  audit still reports 14 moderate transitive Expo advisories whose automated
  fixes require breaking framework downgrades/changes; these are recorded for
  compatibility-tested remediation rather than force-applied.
