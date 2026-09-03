# Connected applications

WORK STATION provides an authenticated, owner-scoped JSON-over-HTTP connector
layer for REST APIs, webhooks, and loopback local APIs. It is a bounded
execution substrate, not a claim that an unconfigured third-party product is
connected.

## Operator configuration

The backend creates the connector runtime only when `CONNECTOR_STATE_ROOT` is
an absolute owner-only directory. `CONNECTOR_ALLOWED_ORIGINS` is the exact
egress allowlist. Each entry must be a credential-free HTTP origin; non-loopback
origins require HTTPS. Paths, queries, wildcards, redirects, proxy environment
variables, and arbitrary model-selected origins are not accepted.

Example environment values:

```dotenv
CONNECTOR_STATE_ROOT=/srv/work-station/connectors
CONNECTOR_ALLOWED_ORIGINS=["https://api.example.com","http://127.0.0.1:9090"]
```

An empty allowlist is a valid fail-closed configuration: owners can inspect the
Connections UI and runtime status, but cannot register an egress target.

## Owner workflow

The protected Connections surface in the web/desktop Apps Hub exposes the
runtime's operator-approved origins. An owner can register a connector with an
exact origin, `read`/`write` scopes, allowed path prefixes, health path,
timeout, bounded retry count, and per-process request rate. OAuth refresh may
select a separate exact token origin from that same operator allowlist; this is
required by providers whose identity and resource APIs use different origins.
The token origin and path remain inside the encrypted credential envelope and
are never returned after save. New connections default to disabled. The
registry retains at most 32 connector identities per owner; owner-row
serialization prevents concurrent creates from exceeding that bound. Mobile
provides monitoring, health, audit, and revocation;
credential registration and rotation intentionally remain on the protected
web/desktop surface.

The canonical action flow is:

```text
operator allowlists origin
→ owner configures connector and write-only credential
→ permission policy is recorded
→ authenticated health check
→ activation only after health succeeds
→ bounded JSON action
→ response verification
→ metadata-only audit
→ disconnect / verified reconnect / credential-destroying revocation
```

The API is under `/api/v1/connectors` and requires the normal owner bearer on
every route. Connector and audit queries are owner-filtered; the database also
uses a composite connector/owner foreign key to prevent cross-owner audit
records.

## Execution and security contract

- supported methods are `GET`, `HEAD`, `POST`, `PUT`, `PATCH`, and `DELETE`
- webhooks permit action `POST` only
- the requested path must remain inside an owner-approved prefix
- the exact origin is checked again immediately before every network request,
  so removing an operator allowlist entry blocks an already-persisted connector
- ordinary actions require a successful health check; disabled connectors may
  perform only bounded health/discovery preflight, and revoked connectors may
  perform neither
- domain actions may require an exact discovered capability in addition to the
  generic read/write scope; the current connector row and capability are
  revalidated in the same execution fetch immediately before network access
- connector paths are canonical: queries, fragments, percent escapes,
  traversal segments, backslashes, and control characters are rejected
- request JSON is limited to 32 KiB and response JSON to 256 KiB
- timeouts are 1–10 seconds and retries are capped at two
- retries apply only to reads or requests carrying an explicit idempotency key
- redirects and environment-derived HTTP proxies are disabled
- bearer, OAuth bearer, and fixed `X-API-Key` authentication are supported
- a separate OAuth token origin is exact-allowlisted at configuration and
  revalidated immediately before every refresh; legacy same-origin envelopes
  remain readable
- credentials are XChaCha20-Poly1305 encrypted with a key outside PostgreSQL
- credentials are write-only, absent from responses, model prompts, and audit
  rows, and are deleted on revocation
- audits cover configuration, credential changes, permission changes,
  authentication, discovery, health, activation, execution, disconnect,
  reconnect, revocation, and failures
- audits store method, path, timing, status, attempts, sizes, and content hashes;
  request/response bodies and credential material are not retained

## Truthful activation states

The persisted connector state model intentionally uses more conservative names
than a provider marketing label:

| API state | Activation meaning |
| --- | --- |
| no configured runtime | `NOT_CONFIGURED` at the setup layer |
| `disabled` or `revoked` | `BLOCKED`; no ordinary external action is allowed |
| `ready` | configuration exists but health has not succeeded |
| `healthy` | authenticated health succeeded against the exact approved origin; this alone is not proof of a provider write |
| `unavailable` | `ERROR`; the last health/reconnect attempt failed |

An authenticated connector cannot be persisted without a credential, so an
incomplete setup remains an `AUTH_REQUIRED` setup condition rather than a
misleading connector row. `LIVE` is reserved in provider reports for a real,
authorized provider operation with objective receipt evidence; the generic API
continues to expose `healthy` until that stronger evidence exists.

The in-process rate limiter is a local deployment guard, not a distributed
quota service. A multi-instance deployment must place a shared rate-limit gate
in front of connector execution before relying on a global quota.

## Verified local workflow

`backend/scripts/real_connector_smoke.py` starts a real loopback HTTP service,
uses a disposable PostgreSQL database, provisions two independent owners, and
proves encrypted bearer authentication, health, owner isolation, path scopes,
an idempotent retry after HTTP 503, metadata-only audit history, log redaction,
credential destruction, failed-authentication/reconnect evidence, and
post-revocation denial. The release runtime gate executes this smoke test.

`backend/scripts/real_business_provider_smoke.py` adds provider-protocol
coverage for CRM account/contact/note/task/deal operations, social and CMS
identity plus reversible draft create/read/update/unpublish, marketing
analytics readback, owner isolation, concrete execution-audit IDs, and
post-revocation denial. These are real loopback HTTP interactions against a
disposable database. They demonstrate runtime readiness, not a live
third-party account.

The marketing publisher contract additionally requires the exact
`campaign.publish` capability. A successful HTTP status is insufficient: the
bounded provider response must carry the matching campaign ID, exact
`published` state, and provider reference. Only its digest is retained in
campaign evidence. Missing or malformed semantic evidence fails closed and
does not set `published_at`.

## External boundaries

OAuth authorization-code exchange and consent screens, provider SDK semantics,
provider-specific GraphQL schemas, email/calendar/CRM/social accounts, and
desktop/browser automation each require their legitimate service, credentials,
scopes, and (where applicable) an owner approval. Refreshing an already issued
OAuth token is supported, including a separately allowlisted token origin; AI OS
does not manufacture the initial grant. The generic connector runtime does not
bypass MFA, OTP, billing, platform policy, or provider authorization. A registry
entry remains `external_dependency` until its real provider workflow is
configured and objectively verified.
