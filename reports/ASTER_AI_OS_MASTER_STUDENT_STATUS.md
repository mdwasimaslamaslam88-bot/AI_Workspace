# ASTER / AI OS master-student status

Iteration 4 — IN_PROGRESS. Updated 2026-09-07T17:18:47.653745+00:00.

Readiness is not claimed. Failed attempts and incomplete live DEX verification remain visible.

Current recorded commit: `e648a5bb5ac5637ff697abb4d86fe497ee20772d`
Evidence: `/home/md-wasim/AI_Workspace_Data/aster-evidence/20260907`

| Issue | Status | Root cause and repair |
| --- | --- | --- |
| ASTER-001: Current master MCP channel rejected | AUTHENTICATED_HTTP_VERIFIED_MCP_UNAVAILABLE | One-use task credentials absent in this session. No verified repair. |
| ASTER-002: DEX subprocess output buffered before bounds checked | FOCUSED_VERIFIED_RELEASE_PENDING | communicate() buffers complete stdout/stderr. Bound reads during subprocess execution, stop producer on excess output. |
| ASTER-003: DEX active callback lifetime not tied to delegation | FOCUSED_VERIFIED_RELEASE_PENDING | Socket listener close does not cancel active handlers. Track and join callback handlers before releasing delegation, close cancelled connections. |
| ASTER-004: DEX unsupported success accepted without review | FOCUSED_VERIFIED_RELEASE_PENDING | Verifier admits empty/self-reported evidence. Require independently verifiable evidence, enforce missing schema limits, explicitly distinguish evidence integrity from semantic correctness. |
| ASTER-005: Immutable startup runtime identity missing | RUNTIME_VERIFIED_RELEASE_PENDING | Readiness endpoint omits build identity. Authenticated diagnostics runtime-identity endpoint exposes immutable startup commit and bounded backend/PWA hashes. |
| ASTER-006: medium-coding-04 partial | REPRODUCED | Exact comprehension form mismatch. No verified repair. |
| ASTER-007: model-comparison-coder-06 fail | REPRODUCED | Recursion terminology mismatch. No verified repair. |
| ASTER-008: voice-stt-02 partial | TARGETED_PASS_FULL_BENCHMARK_PENDING | Literal synthetic checkpoint transcription. No verified repair. |
| ASTER-009: DEX read-only mode does not enforce owner read scope | FAIL_CLOSED_EXTERNAL_HOST_BLOCK | Legacy read-only allows reading synthetic foreign workspace files. Existing DEX command also enables automatic escalation review. Strict permission profiles require modern sandbox enforcement.. Use an explicit scoped read profile, deny environment/private-key paths and proc, prohibit escalation and legacy fallback, disable inherited apps/plugins/agents/web and login-shell configuration. |
| ASTER-010: Blocked DEX result incorrectly completes ToolService audit | FOCUSED_VERIFIED_RELEASE_PENDING | Generic ToolService completion path treated returned BLOCKED envelopes as success.. Fail unsuccessful DEX envelopes under the existing audit constraint and retain owner-visible response without claiming completion. |
| ASTER-011: Cancelled DEX child can perform late filesystem action | FOCUSED_VERIFIED_RELEASE_PENDING | Terminating only the process leader leaves child processes alive.. Start DEX in its own session; terminate/kill its process group with bounded waits on success, failure and cancellation. |
| ASTER-012: Owner workspace root can alias another owner | VERIFIED_RELEASE_PENDING | Containment check accepted sibling owner directory after symlink resolution. Reject symlinked or identity-changing owner roots |
| ASTER-013: Evidence reads bypass filesystem protection and resource bounds | VERIFIED_RELEASE_PENDING | Direct read_bytes accepted protected files and arbitrary file size; FIFO open could block. Reuse authoritative relative-path and bounded descriptor read policy; open reads nonblocking before rejecting nonregular files |
| ASTER-014: Playwright failure log exposes request credentials | VERIFIED_RELEASE_PENDING | Uncaught request errors include authentication headers. Replace credential values before emitting a new bounded error object; rotate exposed provisioning credential in both dotenv and authoritative service configuration |

## Acceptance

- **master_student**: PARTIAL_AUTHENTICATED_REVIEW
- **dex**: FAILED_CLOSED_HOST_SANDBOX_BLOCK
- **desktop**: NATIVE_AUTHENTICATED_CHAT_CONTEXT_PERSISTENCE_RECOVERY_PASS
- **pwa**: PRODUCTION_AUTHENTICATED_E2E_PASS
- **chat**: REAL_RUNTIME_SMOKE_PASSED; final post-push gate pending
- **tools**: REAL_RUNTIME_SMOKE_PASSED; final post-push gate pending
- **agent_os**: REAL_RUNTIME_SMOKE_PASSED; final post-push gate pending
- **missions**: REAL_RUNTIME_SMOKE_PASSED; final post-push gate pending
- **workflows**: REAL_RUNTIME_SMOKE_PASSED; final post-push gate pending
- **memory**: REAL_RUNTIME_SMOKE_PASSED; final post-push gate pending
- **rag**: REAL_RUNTIME_SMOKE_PASSED; final post-push gate pending
- **vision**: REAL_RUNTIME_SMOKE_PASSED; final post-push gate pending
- **image**: REAL_RUNTIME_SMOKE_PASSED; final post-push gate pending
- **voice**: REAL_RUNTIME_SMOKE_PASSED; final post-push gate pending
- **learning**: REAL_RUNTIME_SMOKE_PASSED; final post-push gate pending
- **finance_paper**: REAL_RUNTIME_SMOKE_PASSED; final post-push gate pending
- **private_remote**: PRIVATE_TLS_READY_VERIFIED_VIA_INSTALLED_TAILSCALE; host DNS and remote-peer browser unverified
- **security**: Owner scope repaired; DEX host sandbox failed closed; 0 high/critical and 14 moderate dependency findings; provisioning credential rotated after redacted harness failure
- **performance**: Historical benchmark mean 7.6864s, P95 15.5711s. Runtime image peak 10940MiB; final canonical measurements pending
- **full_regression**: Full release gate 2 passed; after final scope changes backend 3102 passed / 55 skipped, affected 77 passed, PostgreSQL 55 passed; post-push full runtime gate pending
- **release**: UNVERIFIED_THIS_RUN
- **runtime_identity**: INDEPENDENT_STARTUP_HASH_MATCH_PRECOMMIT

## Benchmark

Before: {'score': 97.83, 'pass': 456, 'partial': 2, 'fail': 1, 'provenance': 'historical 459-case run; unchanged raw files located'}
After: Pending fresh canonical run; no new score claimed.

## External blockers

- **SANDBOX_HOST**: Modern Codex bwrap sandbox: loopback Failed RTM_NEWADDR Operation not permitted. Legacy Landlock cannot enforce scoped profiles. Owner action: Provide administrator-approved AppArmor/user-namespace support for the modern Codex/bubblewrap sandbox. Do not disable security globally or enable unrestricted execution.
- **REMOTE_HOST_DNS**: Private HTTPS readiness passes through installed Tailscale userspace daemon with certificate verification. Ordinary host DNS returns ENOTFOUND; no second tailnet device is attached. Owner action: Validate authenticated remote PWA from an enrolled peer with tailnet DNS; no public Funnel or DNS bypass enabled.

## Failed attempts

- initial MCP analysis: REJECTED. AI OS callback was rejected.
- initial report creation: FAILED. Wrong working directory; no file written; corrected before next action.
- Baseline DEX adversarial regression: FAILED_AS_EXPECTED. 8 failed; proves defects before fixes.
- Default and restricted Codex sandbox probes: FAILED. bwrap network namespace setup EPERM; no sandbox bypass used.
- first runtime release gate: FAILED. All subsystem smokes passed, but repository worktree changed during the run; overall gate not credited as pass.
- initial new test fixtures: FAILED. Missing standalone DB fixture and incorrect ToolService fixture/initiator corrected; subsequent regression reproductions verified actual product failures.
- production DEX strict scope: FAILED. Preserved owner audit and failed UI trace in production-dex-strict-scope.json.
- Additional owner/evidence regression: FAILED_AS_EXPECTED. Three original security cases fail; repaired cases pass.
- Private PWA DNS failure: FAILED. Credential was exposed by Playwright in a private error log and tool output. Rotated in authoritative service configuration; stored evidence redacted; raw credential no longer valid.
- Native reload during backend restart: FAILED_THEN_RECOVERED. UI honestly reported refresh failure; retry after readiness cleared error, then database and native history were verified.
- Provisioning credential rotation first retry: FAILED. First updated dotenv but service override remained; then corrected authoritative backend.env. Next immediate attempt was connection refused during startup; redaction worked; readiness-gated retry passed.

The JSON companion preserves per-issue evidence, analysis, verification and release details.
