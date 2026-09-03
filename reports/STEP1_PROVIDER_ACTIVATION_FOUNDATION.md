# Step 1 — Provider Activation Foundation

Evidence date: 2026-09-03. Baseline commit:
`6b66478fd9de5cec98f7023e1417b75110a89ffa`.

## Result

**STEP 1 LOCAL PASS.** The provider-neutral activation foundation is connected,
fail-closed, migration-backed, and verified. No third-party provider was
configured, contacted, authenticated, health-checked, written to, or described
as live during this work.

## Implementation evidence

- The owner vault remains XChaCha20-Poly1305 encrypted, outside PostgreSQL,
  owner-only on disk, write-only through the product API/UI, and excluded from
  responses, model prompts, audit bodies, and logs.
- Every request revalidates the exact current origin allowlist. An empty
  allowlist remains deny-all, and removing an origin blocks persisted
  connectors before any network call.
- Ordinary execution now requires a successful health check. Only bounded
  health/discovery preflight may run while disabled; revocation blocks all
  preflight and execution and destroys credential ciphertext.
- Lifecycle evidence covers configure, credential change, permission change,
  authenticate, discover, health, activate, execute, disconnect, reconnect,
  revoke, and failure outcomes. Failed authenticated reconnects cannot produce
  activation records.
- Migration `0019_connector_lifecycle_audit` extends the database constraint to
  the complete lifecycle action set. Upgrade/downgrade and exact-schema tests
  pass with no Alembic drift.
- Shared web/desktop contracts and Provider Center audit rendering accept and
  display the lifecycle records without exposing secret material.

## Provider-family readiness

| Provider family | Foundation | Real provider state | Exact boundary |
| --- | --- | --- | --- |
| Telephony | RUNTIME_READY | EXTERNAL_BLOCKED | Authorized carrier account/number, credentials, billing, provider approval, exact origin/scopes, and a permitted test call |
| Email | RUNTIME_READY | EXTERNAL_BLOCKED | Authorized mailbox/OAuth client, consent, exact origin/scopes, and an owner-approved test message |
| Calendar/meetings | RUNTIME_READY | EXTERNAL_BLOCKED | Authorized provider account/OAuth consent and an owner-approved reversible test event |
| CRM | RUNTIME_READY | EXTERNAL_BLOCKED | Authorized CRM tenant/sandbox, credential, scopes, and approved test record |
| Social/CMS/marketing | RUNTIME_READY | EXTERNAL_BLOCKED | Authorized owner/test destination, provider approval/OAuth scopes, and explicit publish approval |
| Market data | RUNTIME_READY | EXTERNAL_BLOCKED | Licensed data-provider account/key, permitted instruments, origin/scopes, and freshness-verified response |
| Broker | RUNTIME_READY | OWNER_ACTION | Broker account/API approval, MFA/OTP, risk policy, order/loss limits, kill switch, and explicit live-order authorization |
| Push | RUNTIME_READY | DEVICE_BLOCKED | Authorized FCM/APNs/EAS project credentials plus a registered physical-device token |
| Realtime media | RUNTIME_READY | EXTERNAL_BLOCKED | Authorized WebRTC/video/media provider or admitted local runtime, exact scopes, and supported camera/screen/audio device |

These are external activation boundaries, not internal Step 1 failures. No
credentials or tokens were imported from Codex apps or unrelated sessions.

## Verification evidence

- Focused backend provider/configuration/lifespan/security: **693 passed**.
- Focused Provider Center web: **8 passed**.
- PostgreSQL integration and migrations: **50 passed**, revisions `0001`–`0019`,
  no schema drift.
- Full backend: **2,955 passed, 50 skipped** (intentional environment/runtime
  skips), one upstream Starlette/httpx deprecation warning.
- Web: **196 passed** across 36 files; typecheck, lint, production build, bundle
  budget, lazy-workspace check, and browser/PWA E2E passed.
- Mobile: **62 passed** across 19 files; shared/mobile typecheck, lint,
  Android/iOS static exports, config validation, and Expo Doctor 21/21 passed.
- Desktop: Rust **2 passed**; production binary, AppImage, DEB, package and
  required launch checks passed.
- Local runtime E2E: vision, RAG, memory, image generation/editing/inpainting,
  voice STT/TTS, Agent OS/SSE, connector lifecycle, marketing, finance,
  learning, creative, tools, and workflows passed.
- Security: credential isolation, tracked-secret scan, client-artifact scan,
  CSP, authorization, egress, dependency, and release security gates passed.
  The JavaScript audit reports 14 moderate transitive Expo build-tool
  advisories; no non-breaking remediation is offered, and there are no
  critical/high findings.

The final commit and repository synchronization evidence are reported after the
commit is created; embedding a commit's own SHA inside that commit is not
self-consistent.
