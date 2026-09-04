# Agent OS

`backend/app/agent_os` implements the central bounded orchestrator. Contracts
are frozen dataclasses and enums; goals, plans, artifacts, evidence, attempts,
deadlines, retry counts and output sizes all have explicit limits.

## Lifecycle

`RuleBasedAgentPlanner` selects a specialist from the task unless the caller
chooses one. `AgentPolicy` then verifies that the complete plan stays within the
fixed profile for that specialist. Model text cannot add a permission.

`LocalFirstModelSelector` asks the existing `TaskAwareModelRouter` for an
installed, verified and admitted local model. Only local route exhaustion lets
the selector inspect eligible External AI choices. External provider/model
names are replaced with a stable public hash in Agent OS records.

`AgentOrchestrator` runs at most two tasks concurrently. Each request has a
deadline of at most 600 seconds and no more than two retries. A failed objective
verification excludes that model from the next attempt. Exhaustion, timeout,
cancellation and permission denial are terminal typed states; there is no
unbounded autonomous loop.

The runtime receives typed lifecycle updates directly from the orchestrator.
It records real `queued`, `needs_approval`, `planning`, `running`, `paused`,
`verifying`, `retrying` and terminal transitions in a bounded owner-scoped
snapshot. PostgreSQL stores that snapshot plus an append-only, content-free
event audit. Startup resumes queued work and converts an interrupted active
attempt into a safe paused checkpoint; it never reports an interrupted attempt
as complete. The plan, specialist, admitted public model identifier, attempt
number and verification state shown by Mission Control come from that history;
the clients do not invent percentages or intermediate states.

`IndependentVerificationEngine` hashes output and checks declared artifacts as
regular non-symlink files beneath an optional workspace root. Digest, size and
path checks are read-only. Objective verifiers are application-owned callbacks
that fail closed on exceptions or invalid results.

## Specialist and execution boundary

Registered model-backed specialists are planner, coding, debugging, research,
browser, data, vision, RAG and automation. Image, voice, tools and workflows use
their existing specialized endpoints and runtimes. The generic agent API does
not pretend that an unregistered image/voice/verifier specialist executed.

The public create-run endpoint always replaces client permissions with
`model_inference`. Profiles document possible internal permissions, but no
terminal/filesystem/browser capability is currently delegated through this
endpoint. This is deliberate least privilege.

Generated code that needs execution is passed to `IndependentCodeVerifier`
with a trusted `TrustedCodeVerificationProfile`. The profile—not the generated
answer—defines filenames, verifier sources, absolute commands, expected output
and timeout. The verifier runs in the pinned local Ubuntu container image with
no network, no capabilities, a read-only root, 512 MiB memory, 64 processes,
two CPUs, a private bounded `/tmp`, and a bounded disposable `/workspace` copied
from a read-only input mount. The owner home, repository, runtime sockets and
Docker socket are absent. Each command receives a fresh original input; a
trusted profile combines dependent build/test phases in one command when it
needs intermediate files. Original artifact hashes, mutation detection and
command output hashes are evidence; compiler output is not returned as private
diagnostic text.

## API and clients

- `GET /api/v1/agent-os/capabilities`
- `POST /api/v1/agent-os/runs`
- `GET /api/v1/agent-os/runs`
- `GET /api/v1/agent-os/runs/{id}`
- `GET /api/v1/agent-os/runs/{id}/events?after={sequence}` (authenticated SSE)
- `POST /api/v1/agent-os/runs/{id}/pause`
- `POST /api/v1/agent-os/runs/{id}/resume`
- `POST /api/v1/agent-os/runs/{id}/approve`
- `POST /api/v1/agent-os/runs/{id}/modify`
- `POST /api/v1/agent-os/runs/{id}/retry`
- `POST /api/v1/agent-os/runs/{id}/cancel`

All routes require the owner bearer and every read/control is owner-isolated.
Web and mobile Agents views expose task, optional registered specialist, run
state, typed plan, live activity, verification attempts, final output and only
the controls valid for the current state. Approval-held missions cannot execute
until the owner approves. Changing an approval-held goal invalidates the prior
approval and requires a fresh one. Pause cancels the current unverified attempt,
persists a checkpoint, and resume starts a fresh verified attempt. Manual retry
is restricted to failed/cancelled/timed-out missions and capped at three.
Revision is capped at sixteen. Audit details contain SHA-256 digests rather than
goal content.

The web client consumes authenticated SSE with a bounded reconnect cursor;
mobile uses the same retained event contract with bounded refresh polling
because React Native streaming-fetch support varies by runtime. Chat drafts can
be submitted directly as text missions, and locally transcribed speech retains
a typed `voice` input source when it becomes a mission. Persistence is reported
as `postgresql_checkpoint_scheduler` when the database is configured, otherwise
the explicitly degraded `bounded_process_memory` mode remains available.

The Learning Teacher reuses this orchestrator with only `model_inference`, a
bounded 120-second deadline, one Agent OS retry, and `allow_external_models=false`.
Lessons and assessments are persisted only when the independent verifier's
output digest matches the untouched artifact. Learning sources and preferences
are untrusted private data; they cannot grant permissions or invoke tools.

## Verification

```bash
cd backend
.venv/bin/python -m pytest -q tests/agent_os tests/test_agent_os_api.py
../scripts/postgres_integration_check.sh
```
