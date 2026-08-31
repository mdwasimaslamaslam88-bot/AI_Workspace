# Safe self-update

`SelfUpdateManager` implements checkpoint → isolated candidate → mandatory gates
→ update ready → explicit activation. It never checks out a candidate over the
production source tree and never force-pushes or rewrites Git history.

Set `SELF_UPDATE_STATE_ROOT` to an absolute owner-only directory outside source.
The same directory is used by the CLI:

```bash
backend/.venv/bin/python scripts/self_update_tool.py \
  --state-root /absolute/private/self-update status
```

## Last-known-good checkpoint

Preparation requires a clean production checkout and creates:

- a verified Git bundle for the exact current commit
- dependency lock/configuration snapshots and routing/admission source
- an authenticated encrypted snapshot of existing private `.env` files
- an optional independently verified PostgreSQL/asset backup reference
- a manifest, complete `SHA256SUMS`, and a key-authenticated checksum index

Checkpoint roots, keys and files are owner-only. HMAC-SHA-256 binds the checksum
index to the checkpoint key, so coordinated manifest/checksum tampering also
fails. Private configuration uses XChaCha20-Poly1305 and is restored into a
candidate only after checkpoint verification and final activation.

## Candidate isolation and gates

Every validation attempt receives a fresh uniquely named candidate cloned
without hard links into the private update root; an interrupted or failed
candidate is never reused by a later attempt.
Currently verified `node_modules` and Python virtual environment are copied
with reflink-on-write when supported, never hard-linked. Every gate runs through
a pinned Docker image with no capabilities, a read-only container root,
bounded tasks/memory/CPU/time and temporary storage, no Docker socket, and only
the candidate writable. The owner home is absent except for exact gate-specific
read-only mounts. Network is disabled except for the fixed security and
combined release gates that require dependency advisory access. Read-only
runtime mounts are gate-specific: Playwright for the browser gate, and the Rust
registry/toolchain plus pinned Tauri cache/sysroot for desktop/release;
unrelated runtime and Cargo state is not exposed. If the pinned image or Docker
boundary is unavailable, the candidate gate fails closed.

READY requires all named gates: source, backend, database, web, mobile, desktop,
browser E2E, RAG/memory, vision/image/voice, tools/workflows/agents,
routing/admission/hardware, API fallback, self-update, security, performance,
rollback and combined release. A failure or timeout records only hashes and a
fixed failure code; production remains unchanged.

Prepare a locally available candidate commit with:

```bash
backend/.venv/bin/python scripts/self_update_tool.py \
  --state-root /absolute/private/self-update \
  prepare --candidate origin/main --version 0.2.0 \
  --database-backup /absolute/verified/backup
```

The optional technology watcher fetches only a fast-forward `origin/main`, then
runs the same complete preparation. Its systemd timer is installed but disabled
and is not part of `work-station.target`. It never activates a result and the UI
does not announce a candidate while validation is incomplete.

## Final decision and actual deployment boundary

Only status `ready` enables the UI buttons `UPDATE` and `CANCEL`. UPDATE
re-verifies the checkpoint, restores encrypted configuration into the candidate,
and atomically changes the managed `current` symlink. CANCEL records the owner
decision without deleting checkpoints or changing production.

The symlink switch affects only services configured to run from that managed
`current` path. The repository's normal installer points at the checkout used to
run it; it does not silently migrate an existing installation into managed
release mode. Restarting external services, OS/package updates, store signing,
account authentication and unavailable signing keys remain explicit external
boundaries.
