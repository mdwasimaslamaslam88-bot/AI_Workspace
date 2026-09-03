# Step 2 Provider Activation Evidence

Date: 2026-09-04

Baseline: `4bd5d88eb92d1744084e3b53a24bd28d62efe39a`

Result: **PARTIAL** — all locally achievable implementation and verification
gates pass; real telephony, email, and calendar/meeting providers remain
`EXTERNAL_BLOCKED` because no approved external origin, connector, or owner
credential is configured.

No external request was attempted, and no capability is labeled live.

## Production inventory

| Item | Observed value |
| --- | ---: |
| Approved external origins | 0 |
| Configured provider connectors | 0 |
| Enabled provider connectors | 0 |
| Healthy provider connectors | 0 |
| Credentialed provider connectors | 0 |

The connector runtime and PostgreSQL are configured. Production egress remains
deny-all until an operator explicitly allowlists an exact HTTPS origin. The
connector state directory is owner-only (`0700`) and its generated encryption
key is owner-read/write only (`0600`).

## Telephony — RUNTIME_READY / EXTERNAL_BLOCKED

Verified locally:

- web/desktop and mobile configuration paths require explicit owner approval,
  a selected owner connector, E.164 destination validation, and an admitted
  `phone_call` or `callback` capability;
- only an enabled, health-verified, correctly scoped owner connector can use
  the fixed phone/callback paths;
- the provider receipt must echo the request ID and
  `accepted_by_provider`, and success must carry a concrete connector execution
  audit ID;
- disconnected and revoked connectors cannot execute; reconnect requires a
  fresh successful health probe; bounded retry and failure audits pass;
- a real loopback HTTP provider exercised the full local gateway contract.

Not externally verified: inbound call, outbound call, callback delivery,
carrier state streaming, interruption transport, or spoken task result.

Owner action: configure a legitimate carrier account, approved API and webhook
origins, encrypted credentials, a provisioned number, billing/MFA/provider
approval, minimum scopes, a reviewed signed webhook/stream adapter, consent
policy, and an explicitly authorized E.164 test destination.

## Email — RUNTIME_READY / EXTERNAL_BLOCKED

The generic provider substrate is ready for bounded REST/GraphQL/webhook APIs,
write-only encrypted API-key/bearer/OAuth credentials, exact origin and path
permissions, health checks, retry, audit, disconnect, revoke, and reconnect.
OAuth refresh now supports a separate identity-provider token origin, with the
token origin selected from the operator allowlist and revalidated immediately
before every refresh. Legacy same-origin encrypted envelopes remain readable.

No mailbox identity or provider authorization exists. Inbox read, compose,
send, reply, attachment handling, and provider receipt verification were not
run.

Owner action: select the email provider, allowlist exact API/token origins,
register a legitimate OAuth client or API key, complete consent/MFA, grant
minimum mailbox scopes, select the owner mailbox, and approve a bounded test
recipient. A provider-specific mailbox/attachment adapter must be reviewed if
the chosen API is not expressible through the bounded generic contract.

## Calendar/Meetings — RUNTIME_READY / EXTERNAL_BLOCKED

The same encrypted, exact-origin, owner-scoped OAuth/API-key lifecycle is ready.
No calendar identity or provider authorization exists. Event
create/read/update/cancel, meeting links, reminders/callbacks, and provider-side
verification were not run.

Owner action: select the calendar/meeting provider, allowlist exact API/token
origins, register a legitimate OAuth client or API key, complete consent/MFA,
grant minimum event/meeting scopes, select the owner calendar, and approve one
reversible test event. A provider-specific event/meeting adapter must be
reviewed if required by the selected API.

## Security evidence

- Production external egress remains deny-all; every network request,
  including OAuth refresh, revalidates an exact approved origin immediately
  before opening the network.
- Credentials remain XChaCha20-Poly1305 encrypted, write-only through the UI,
  absent from API responses, model prompts, logs, and metadata-only audits, and
  deleted upon revocation.
- Failed authentication cannot create healthy or live evidence. Revoked,
  disabled, unhealthy, wrong-owner, wrong-scope, wrong-path, redirecting, or
  unallowlisted connectors fail closed.
- Credentials from unrelated Codex/app sessions were not inspected or imported.
- Security audit passed with no critical/high findings. The dependency audit
  retains 14 moderate transitive Expo build-tool advisories whose available
  automated remediation is breaking-only; they are not runtime provider
  credential findings.

## Verification evidence

| Gate | Result |
| --- | --- |
| Focused final provider/security backend | 34 passed |
| Complete backend regression | 2,958 passed, 50 environment/runtime skips |
| Web | 197 passed; typecheck, lint, production build passed |
| Mobile/shared | 63 passed; typechecks, lint, Android/iOS static exports passed |
| Expo Doctor | 21/21 passed |
| PostgreSQL | 50 passed; migrations through `0019`; no drift |
| Browser/PWA E2E | PASS |
| Desktop | Rust tests, binary, AppImage, DEB, and launch checks PASS |
| Security/release | PASS; no critical/high findings |
| Real local runtime E2E | PASS, including connector and communication gateway |

Native Android packaging remains an environment skip because no Android SDK is
installed; this is not required to establish the Step 2 web/desktop/mobile
contract result. No real-provider smoke test ran because its prerequisites were
absent.

Step 3 was not started.
