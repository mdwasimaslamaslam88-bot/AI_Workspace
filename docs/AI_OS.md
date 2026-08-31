# WORK STATION Personal AI OS

WORK STATION is a private, local-first Personal AI operating layer. The
implemented product consists of one authenticated FastAPI contract used by the
compiled web/PWA, Tauri desktop shell, and Expo Android/iOS client. PostgreSQL
is authoritative for owner data. Runtime adapters keep model and hardware
details below the product boundary.

## Implemented control flow

```text
owner goal
→ typed Agent OS request
→ deterministic specialist and permission plan
→ admitted local task route
→ optional policy-authorized external fallback
→ bounded specialist execution
→ independent output/artifact verification
→ at most two alternate-model retries
→ owner-scoped result
```

The agent API is `/api/v1/agent-os`. Browser and mobile Agents surfaces create,
list, monitor, and cancel owner-scoped runs. The public API deliberately grants
only `model_inference`; a model cannot turn a request into terminal, filesystem,
browser, network, or tool authority. Existing bounded tools and durable
workflows remain the execution boundary for those operations.

The independent code verifier in `app.agent_os.code_verification` accepts only
server-owned profiles with fixed absolute executables and test sources. It
hashes the original generated artifact, applies static safety checks, runs
compile/test commands in a pinned networkless/resource-bounded container that
cannot see the owner home or host runtime sockets, and detects verifier-side
mutation. It never repairs the artifact before scoring.

## Product subsystems

- chat, conversations, immutable fork/edit/regenerate, citations and assets
- typed Agent OS with planner, coding, debugging, research, browser, data,
  vision, RAG and automation model-backed specialists
- task-aware local model routing and one hardware/model admission engine
- encrypted, opt-in External AI providers with cost/quota/rate policy
- documents and RAG for TXT, PDF, DOCX, CSV and bounded source files
- explicit conversation/project-like history and owner-controlled memory
- capability-separated vision, image generation/editing and voice runtimes
- fixed-registry tools and durable bounded workflows
- normalized hardware discovery, GPU-upgrade fingerprints and simulations
- verified backup/restore, staged self-update checkpoints and atomic rollback
- authenticated diagnostics for hardware, models/routes, provider cost/health,
  agents, update state and metadata-only security containment events
- PWA/web, Ubuntu/Windows Tauri packaging paths and Android/iOS Expo client

## Local-first policy

Local admitted models are exhausted first. An external provider is considered
only when its global and per-provider switches are enabled, quota/spend/rate
policy permits the call, the task and context fit, and an immutable registered
complete-category benchmark record exactly matches the model policy. Provider
keys are XChaCha20-Poly1305 encrypted, write-only through clients, excluded from
prompts and responses, and never accepted through a custom provider URL.

## Verified production evidence (2026-08-31)

The final local release matrix passed with the production runtime enabled and
with native Android and desktop-launch gates required. Measured results were:

- backend: 2,830 passed, 36 skipped
- web: 153 tests passed; mobile: 52 tests passed
- Android: Expo Doctor 21/21 and 338 Gradle tasks; signed/aligned APK contract
- PostgreSQL integration: 36 tests passed
- browser/PWA install, authenticated chat, cache isolation and logout: passed
- real vision, RAG, memory, image generation/editing/inpainting, voice,
  tools and workflows: passed
- canonical quality benchmark: 459 tests, 97.74/100, 455 PASS, 3 PARTIAL,
  1 FAIL, safety 100%, hallucination 0%, executable code 24/24
- massive stability run: 10,000/10,000 passed

The previously recorded quality score was 97.76. The authoritative final run
was 97.74 with the same PASS/PARTIAL/FAIL counts; no expected answer, scoring
rule, generated artifact or production output was altered to conceal that
measured variance. The four remaining non-passes are retained in the external
benchmark failure inventory.

## Persistence and boundaries

Conversations, RAG, memory, tools and workflows are durable in PostgreSQL.
Interactive Agent OS run records are intentionally bounded process memory (100
records per owner) and are lost on backend restart; the API and UI report this
instead of implying durable execution.

The self-update manager can atomically switch only a managed deployment rooted
at its `current` symlink. A development checkout or a systemd service installed
directly against this repository is not silently rewritten. Account login,
2FA, billing, unavailable signing credentials, mobile store signing and physical
device interaction remain external boundaries.

Windows binaries are produced and tested by the repository's Windows CI path;
local Linux validation cannot execute a Windows binary. Apple signing, Google
Play/App Store publication and physical-device acceptance remain account,
credential or device boundaries. This host exposes a rootful Docker daemon;
membership of the service account in the Docker group remains a host
administrative boundary even though candidate containers receive no daemon
socket and use fixed, restricted launch contracts.

Simulated hardware tiers and future-model contracts are admission tests only.
They are never reported as real model execution. WORK STATION does not claim to
be unhackable or universally correct.
