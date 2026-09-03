# Step 3 CRM, Social/CMS, and Marketing Provider Activation

Date: 2026-09-04

Baseline: `7957595d0d8a99297dcf5d68a7a1e42232d39f0d`

Result: **PARTIAL** — every locally achievable implementation, security, and
runtime gate passes. CRM, social/CMS, and marketing are `RUNTIME_READY`, but
all third-party providers remain `EXTERNAL_BLOCKED` because production has no
approved external origin, provider connector, or owner credential.

No external provider request, record mutation, publication, analytics fetch,
advertising launch, or spend occurred. Step 4 was not started.

## Production inventory

| Item | Observed value |
| --- | ---: |
| Approved external origins | 0 |
| Configured provider connectors | 0 |
| Enabled provider connectors | 0 |
| Healthy provider connectors | 0 |
| Credentialed provider connectors | 0 |
| Verified external-live providers | 0 |

Production egress therefore remains deny-all. The connector runtime and
PostgreSQL are configured, and the owner-only connector state/key permissions
remain `0700`/`0600`.

## CRM — RUNTIME_READY / EXTERNAL_BLOCKED

Executed locally against a real loopback HTTP protocol endpoint and disposable
PostgreSQL:

- account identity verification;
- contact create, read, and update;
- note, task, and deal creation;
- exact path/scope enforcement, discovered capability inventory, encrypted bearer authentication,
  owner isolation, concrete execution-audit IDs, and provider result checks;
- credential-destroying revocation followed by verified execution denial.

Not executed: a third-party CRM identity/read/write/search/webhook operation.
No real CRM account or provider response exists in AI OS.

Owner action: select an owner-controlled CRM sandbox/test account; allowlist
its exact HTTPS API and token origins; register a legitimate OAuth client or
API key; complete consent/MFA/provider approval; grant minimum record scopes
and paths; and approve a disposable test contact. A reviewed provider adapter
must normalize the selected API where its schema differs from the bounded
connector contract.

## Social/CMS — RUNTIME_READY / EXTERNAL_BLOCKED

Executed locally:

- social account identity plus reversible draft create/read/update/unpublish;
- CMS site identity plus reversible draft create/read/update/unpublish;
- health, discovered capabilities, per-action audit IDs, idempotency on writes,
  owner isolation, exact-origin/path enforcement, and post-revocation denial;
- web and mobile publisher selection now excludes disabled, unhealthy,
  read-only, revoked, and non-`campaign.publish` connectors.

Not executed: a third-party account/page/site read, media upload, schedule,
publish, edit, unpublish, or analytics readback. Nothing was published publicly.

Owner action: choose an owner/test destination, allowlist exact API/token
origins, complete legitimate authorization and any app review, configure
minimum scopes/paths/capabilities, and explicitly approve reversible test
content and its destination.

## Marketing — RUNTIME_READY / EXTERNAL_BLOCKED

Executed locally:

- the complete local research, strategy, content, creative, approval, publish,
  source-analytics, and optimization workflow;
- publish is possible only after explicit campaign approval through a healthy
  owner connector advertising the exact `campaign.publish` capability;
- an HTTP success alone no longer marks a campaign published: the response
  must contain the matching campaign ID, exact `published` state, and a bounded
  provider reference;
- evidence retains a concrete connector execution ID and hashes rather than a
  raw provider reference; malformed/unverified receipts fail the publish stage
  and leave `published_at` unset;
- analytics remain deterministic calculations over owner-submitted,
  source-referenced metrics. They are not represented as provider retrieval.

Not executed: a third-party campaign draft, audience query, template mutation,
publication, provider analytics retrieval, paid campaign, or advertising spend.

Owner action: configure an owner-authorized marketing/CMS sandbox or test
destination with exact origins, encrypted credentials, minimum scopes/paths,
`campaign.publish`, and an adapter returning the verified receipt contract;
then explicitly approve the content and destination. Paid activation requires
separate owner authorization and spend controls.

## Security evidence

- Exact origin, current path prefixes, read/write scope, connector state, and
  required domain capability are revalidated in the same execution fetch
  immediately before every request.
- Disabled, unhealthy, revoked, wrong-owner, wrong-scope, wrong-path,
  wrong-capability, redirecting, and unallowlisted connectors fail closed.
- Credentials remain XChaCha20-Poly1305 encrypted and write-only; request and
  response bodies, provider references, and credentials are absent from audit
  bodies and product logs.
- Retries are bounded and non-read requests require idempotency keys. Production
  egress remains deny-all by default.
- No credentials or sessions from Codex, ChatGPT, or another application were
  inspected or imported.
- Security audit passed with no critical/high findings. Fourteen moderate
  transitive Expo build-tool advisories remain; available automated remediation
  is breaking-only and none is a provider credential exposure.

## Verification evidence

| Gate | Result |
| --- | --- |
| Focused backend connector/marketing/security | 44 passed |
| Complete backend regression | 2,960 passed; 50 environment/runtime skips |
| Focused web provider/marketing | 15 passed |
| Complete web | 198 passed; typecheck, lint, production build passed |
| Mobile/shared | 63 passed; typechecks, lint, Android/iOS static exports passed |
| Expo Doctor | 21/21 passed |
| PostgreSQL | 50 passed; migrations through `0019`; no drift |
| Browser/PWA E2E | PASS |
| Desktop | Rust tests, binary, AppImage, DEB, and launch checks PASS |
| Real local runtime E2E | PASS, including CRM/social/CMS protocols and marketing |
| Security/release | PASS; no critical/high findings |

Native Android packaging remains an environment skip because no Android SDK is
installed. This does not convert any provider to live. Real-provider smoke tests
did not run because the required owner authorization and configuration are absent.
