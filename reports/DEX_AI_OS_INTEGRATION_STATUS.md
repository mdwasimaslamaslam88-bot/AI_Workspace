# DEX ↔ AI OS Integration Status

Date: 2026-09-07

Validated application commit: `401cf40df17d40c554021c0c77672b9a65b058a0`

Integration commits: `0d4238949c5500fa08874873f063b87f31689262`, `401cf40df17d40c554021c0c77672b9a65b058a0`
Result: **LOCAL PASS / RUNTIME PASS**. No external or simulated result is counted as proof.

## Identity and architecture

- DEX is the installed OpenAI Codex CLI `0.147.0`; resolved binary SHA-256:
  `cb0a15567e9a60a5820d54b0f6ae86d504dc3805c1eab21a47f70e3eb7b73a40`.
- AI OS is WORK STATION `0.1.0` at the validated application commit above.
- AI OS delegates through the existing owner-authenticated `ToolService`
  capability `dex.delegate`; there is no second tool, permission, mission, or
  audit framework.
- DEX runs as an ephemeral, bounded, read-only Codex subprocess. Reverse calls
  use a per-task mode-`0600` Unix socket and the STDIO MCP server in
  `scripts/ai_os_mcp_server.py`.
- The callback binds one-use capability token, task ID, correlation ID, owner
  ID, execution mode, tool name, and schema. It reuses the AI OS
  `AgentOrchestrator`; authorized file mutations additionally reuse the
  owner-scoped filesystem ToolService and its independent read-back verifier.
- The MCP server SHA-256 is
  `56817882a47aa9be7505826c3b42b1862e0242461446025ff5f217eb202b48b1`;
  the result schema SHA-256 is
  `d2b693ed121b811fce8f9450378305530baad218b945a38b4dad1b0a26c47ba2`.

## Bidirectional production evidence

### DEX → AI OS exact workspace action

The exact requested task created `DEX_AI_OS_REAL_TEST.txt` with the 35 UTF-8
bytes `DEX AND AI OS COOPERATION VERIFIED.` and no trailing newline. DEX used
the real MCP callback exactly once; AI OS executed the owner-scoped write,
checked existence, read the file back, and verified the exact bytes. The DEX
gateway then independently recalculated the file digest before returning
`VERIFIED`.

- Task/audit ID: `31510d2f-cc34-4f54-ae5e-9fb5f2e0c6da`
- Correlation ID: `e6f05e6b-f5f7-4181-b746-0dadec3dc50d`
- Owner ID: `5e0f712c-0fb6-4ac8-9c32-1e6667b7a226`
- File SHA-256:
  `6af6f14cbb6d963497580c0e40dbf6167ae0bb95d5de167308029194a59922ff`
- Filesystem write/exists/read audits:
  `64cb3257-18e5-4760-b5d7-3ee5df7886f0`,
  `9df850a6-7260-4308-b0ba-01df66b1b48d`, and
  `951cbb91-9911-4dfb-ac2d-b2370111e6c6`
- Durable production DEX audit: `87e3df39-09d8-43f4-9e2f-8245f016bf58`
- Duration: 91,681 ms
- Cleanup: verified; the test file is not retained.

### AI OS → DEX authenticated compiled-PWA path

The actual compiled PWA, authenticated with a bearer session kept only in
session storage, asked DEX to inspect a harmless repository condition. AI OS
selected `dex.delegate`, DEX performed real read-only inspection, DEX called
AI OS through MCP for independent review, and AI OS verified the returned
evidence before the UI entered `done`.

- DEX audit/task ID: `26cd6a9f-832f-4c4c-b34d-b6c4e0661426`
- Correlation ID: `608b2329-f7e4-426e-9968-d33991701a62`
- Owner ID: `dcdcf37d-3494-4acd-b624-25d918b2b545`
- Exact owner request: 75 bytes; SHA-256
  `48581465ba9a02d6fc94ad41df3dceafd1a5b499b5ea76d26350a76ba9a7a57b`
- Verification result SHA-256:
  `5e3fc2221104d228f4e4a864c68851a9b3cee7f65c73c1030e313ba7a1424874`
- Transport: `local-subprocess-jsonl+stdio-mcp`
- Attempts: 1; duration: 111,563 ms; status: `VERIFIED`
- Verified timeline: `planning` → `selecting_tool` → `asking_dex` →
  `dex_working` → `verifying_dex` → `done`
- Callback count and AI OS evidence provenance checks both passed.

The same browser run also passed ordinary local-model chat, a real
filesystem write/exists/read loop, exact cleanup, audit rendering, logout,
and post-logout cache isolation.

## Last blocked run and root cause

Audit `9fd72cf8-63d2-4e57-8af9-dde8c6857fd6` remains truthfully recorded as
`cancelled` with `tool_cancelled` after 292,623 ms. It is not rewritten as a
pass. Two coupled problems caused the unstable browser path:

1. the chat model paraphrased the authenticated owner's DEX request, changing
   the task bytes and producing inconsistent DEX behavior; and
2. the single 240-second DEX attempt could consume almost the entire 300-second
   browser budget, leaving no bounded retry/finalization reserve.

The repair pins DEX delegation to the exact authenticated owner request.
Read-only delegation receives at most two 130-second attempts inside the
300-second outer budget, retaining 40 seconds for finalization. Workspace
writes remain one-attempt and non-retryable, and an unavailable runtime is not
retried. The result exposes `delegation_attempts`.

## Disagreement and failure recovery

The 22-test DEX boundary suite and the larger 308-test focused integration
gate prove that incorrect or unverifiable agent claims are not trusted:

- a deliberately incorrect file digest is rejected; corrected current-file
  evidence is accepted;
- missing files, symlink evidence, ambiguous evidence semantics, absent AI OS
  provenance, and missing callback are rejected;
- unavailable DEX, invalid capability/scope/mode/initiator, forbidden
  repository writes, mutation without AI OS review, malformed final JSONL,
  callback replay, wrong owner, unknown MCP tool, and malicious callback text
  fail closed;
- cancellation terminates the subprocess;
- read-only failure/timeout retries are bounded under the same owner audit;
- workspace mutation is never retried, preventing duplicate side effects.

No rejected case can become `DONE` or `VERIFIED` merely from an agent claim.

## Security and isolation

- DEX receives an allowlisted environment, a per-task one-use token, and only
  the MCP tool appropriate to its execution mode.
- DEX itself always uses a read-only sandbox. Owner-workspace writes can occur
  only through the server-pinned callback and the existing scoped ToolService.
- Chat cannot grant DEX workspace-write mode. Repository scope is always
  read-only.
- Callback socket permissions, owner binding, replay denial, payload/output
  bounds, timeouts, process teardown, path traversal/symlink protection,
  evidence digest verification, secret redaction, and audit ownership passed.
- Production DEX/tool capabilities are not exposed by the public tool catalog.
- Security, dependency, tracked-secret, client-secret, CSP, egress, service,
  and artifact scans found zero critical/high findings. Fourteen moderate
  transitive Expo build-tool advisories remain; available automatic remedies
  require breaking dependency changes and were not forced into this release.

## Verification gates

| Gate | Evidence | Result |
|---|---:|---|
| DEX boundary tests | 22 | PASS |
| DEX/ToolService/chat focused tests | 308 | PASS |
| Backend | 3,083 passed, 55 intentional skips | PASS |
| Web | 206 tests across 36 files; typecheck/lint/build | PASS |
| Mobile/shared | 64 tests across 19 files; typecheck/lint/static bundles; Expo 21/21 | PASS |
| Desktop | 2 Rust tests; binary/AppImage/DEB build and sustained native launch | PASS |
| PostgreSQL | 55 tests; 0001→0024, downgrade/re-upgrade, no drift | PASS |
| Browser/PWA | compiled authenticated DEX path and filesystem tool path | PASS |
| Runtime E2E | vision, RAG, memory, image, voice, agents, connectors, finance, learning, creative, tools, workflows | PASS |
| Security/release | security, gateway, systemd, artifact and release checks | PASS |

Measured runtime data from the final PostgreSQL/runtime gate included about
8,534 MiB GPU memory for vision, 11,040 MiB for image, and 1,097 MiB for STT.
The persistent Agent OS mission completed in one attempt in 7,934 ms. The real
compiled-PWA DEX round trip completed in 111,563 ms. Model routing and RTX 3060
12 GiB admission policies were not widened.

## Canonical AI quality

The newest full 459-case canonical run is the authoritative result:

- score: **97.83/100**
- 456 PASS, 2 PARTIAL, 1 FAIL; pass rate 99.35%
- Safety 100%; hallucination rate 0%; executable code 24/24
- average latency 7.6864 s; p95 latency 15.5711 s; duration 2,748.32 s

Compared with the previously reported 97.88 baseline, the measured delta is
-0.05. No benchmark expectation, checker, output, or route was altered. The
remaining non-passes are installed-model limitations:

1. `medium-coding-04` (PARTIAL 63.5): generated `0**2` through `4**2`, not the
   required comprehension form containing `x * x` and `range(5)`.
2. `model-comparison-coder-06` (FAIL 36.5): returned `Infinite Recursion`, not
   the exact standard term `base case`.
3. `voice-stt-02` (PARTIAL 78.75): Faster-Whisper small.en transcribed the
   fixed synthetic phrase as `Pause, then continue, Warts Harbor, value 47.`
   and missed the literal checkpoint words.

The post-benchmark DEX stabilization patch affects only DEX delegation and has
no canonical benchmark cases, model route, inference profile, or checker, so
the completed full run remains valid evidence for this release.

## Release artifacts and runtime

Release directory:
`/home/md-wasim/AI_Workspace_Data/releases/dex-ai-os-401cf40`

| Artifact | SHA-256 | Verification |
|---|---|---|
| `Work_Station_Ubuntu.AppImage` | `0f7b92d50436e2ee89f1417d0ca96f842bd3cf4b7e32329c4b0b8c8d763f2cf3` | built, inspected, sustained native launch |
| `Work_Station_Ubuntu.deb` | `bed0206e21d09b4500e9e371b04d074588c081ad336ab874b4d3b92e0c7de055` | built and package-inspected |
| `Work_Station_Web_PWA.tar.gz` | `e23d124e8c1bad1ea03bb10b748916b8778a1e3d08eab7baf2e4bba74e7f007c` | production build, archive inspection, Chromium E2E |

The running backend was restarted from the validated checkout and reports
PostgreSQL, Redis, and Ollama ready. The launched transient desktop service is
`work-station-desktop-dex-ai-os-401cf40.service`, using the exact AppImage
above; a real `WORK STATION` native window was observed.

Current-source native Android, Windows, and macOS packages were not fabricated.
Android SDK/device validation, Windows/macOS native build hosts, platform
signing/notarization, and physical-device acceptance remain external or
environment boundaries.

## Feature registry and remaining boundaries

The deterministic registry contains 335 unique features: 277 implemented, 19
runtime-dependent, 39 external-dependent, and zero planned. All UI path,
backend-or-boundary, test coverage, and uniqueness validations pass. Registry
SHA-256:
`4386bcfb53e088d2299e7f85917bb0d77de2102b71dff5d8731203b5f7de2ea8`.

DEX integration is runtime-dependent because it legitimately requires the
installed, authenticated Codex runtime. Real telephony, email/calendar/CRM/
social providers, live market data, live broker actions, push providers, and
hosted advanced media remain externally blocked until owner credentials,
consent, approved origins/scopes, billing, and provider permissions exist.
