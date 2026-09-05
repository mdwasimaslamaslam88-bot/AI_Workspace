# Authenticated chat tool execution evidence

Date: 2026-09-06

## Root cause

Authenticated conversation generation called `TextGenerationRouter` directly.
The fixed `ToolService` registry, owner checks, durable tool audit, and workflow
executor existed, but chat supplied no tool schemas and had no result loop.
Consequently the local model correctly reported that it could not access the
filesystem.

## Implemented path

```text
authenticated conversation API
→ task-aware local model router
→ intent-scoped native tool schemas
→ authoritative ToolService registry
→ owner/conversation/permission validation
→ bounded real operation
→ durable audit
→ independent exists/read verification for writes
→ model continuation with real result
→ persisted answer and UI execution evidence
```

The bridge reuses `ToolService`, `ToolRepository`, the existing task router,
generation admission, PostgreSQL owner records, and the existing authenticated
conversation contract. It does not add a parallel agent, permission, registry,
or audit system.

## Filesystem security

- Default is deny-all (`FILESYSTEM_TOOL_ROOTS=[]`).
- Production configuration selects only explicit narrow roots outside source,
  the home root, and protected system trees.
- Every root contains an automatically isolated UUID directory per owner.
- Paths are relative-only, normalized, length-bounded, and reject traversal,
  hidden/sensitive names, protected key suffixes, backslashes, and NUL bytes.
- Descriptor-relative no-follow operations prevent symlink escape and TOCTOU
  path substitution.
- UTF-8 writes/reads are capped at 65,536 bytes; list output is capped at 200
  entries and all structured tool output remains bounded.
- Writes are atomic, do not overwrite differing content, and repeat safely when
  the existing content matches.
- Write content and read content are redacted from durable audit payloads.
- Filesystem permissions are not admitted to bounded workflows or the generic
  Agent OS mission endpoint.

## Verified exact user path

Prompt:

```text
Create AI_OS_REAL_TEST.txt with:
AI OS REAL EXECUTION VERIFIED
```

Both the isolated release fixture and the installed real Ollama runtime passed
the compiled-PWA authenticated test. The real path produced, in order:

1. a native `filesystem.write` model call;
2. a completed owner-scoped write audit;
3. a separate `filesystem.exists` verifier audit;
4. a separate `filesystem.read` verifier audit;
5. exact content and SHA-256 agreement;
6. a `completed` response trace ending in `done`;
7. visible UI tool, relative path, and verification state;
8. exact fixture cleanup.

The automatic `tool_calling` route now selects the empirically verified
`qwen3:8b` profile. If a model answers in prose before requesting the admitted
tool, the orchestrator makes at most two additional no-side-effect selection
attempts with a generic native-tool instruction; exhaustion remains `BLOCKED`.
Repeated installed-runtime tests passed this bounded path. The former smaller
coder route repeatedly returned prose and never caused a filesystem side effect.

No provider/API fallback, textual tool simulation, hardcoded backend success,
or arbitrary host path permission was used. The isolated model fixture is only
protocol/compiled-UI coverage and is not counted as real model evidence; the
second browser run used the installed local model runtime.

## Fail-closed coverage

Focused tests cover absent model capability, absent configured workspace,
missing/unregistered tools, permission failure, verification mismatch, owner
isolation, traversal, absolute/protected paths, large payloads, secret-shaped
content, existing-file conflicts, same-content idempotency, and symlink parent
or target escape. A blocked or failed trace cannot contain `done`, and the UI
does not synthesize progress or success.

## Evidence status

- Authenticated normal chat bridge: `LOCAL_PASS`, `RUNTIME_PASS`
- Installed local model native tool call: `RUNTIME_PASS`
- Filesystem write/read/exists/list/stat: `LOCAL_PASS`
- Independent write read-back: `LOCAL_PASS`, `RUNTIME_PASS`
- Compiled PWA UI/API path: `LOCAL_PASS`, `RUNTIME_PASS`
- External provider dependency: none
- Broad host filesystem, shell, root, and protected paths: intentionally blocked

Final consolidated counts, security output, commit identity, and Git
synchronization are recorded in the release handoff after the canonical gate.
