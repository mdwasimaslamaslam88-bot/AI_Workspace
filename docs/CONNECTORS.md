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
timeout, bounded retry count, and per-process request rate. New connections
default to disabled. The registry retains at most 32 connector identities per
owner; owner-row serialization prevents concurrent creates from exceeding that
bound. Mobile provides monitoring, health, audit, and revocation;
credential registration and rotation intentionally remain on the protected
web/desktop surface.

The canonical action flow is:

```text
operator allowlists origin
→ owner registers connector and credential
→ owner enables explicit scopes and paths
→ health check
→ bounded JSON action
→ response verification
→ metadata-only audit
→ revocation when requested
```

The API is under `/api/v1/connectors` and requires the normal owner bearer on
every route. Connector and audit queries are owner-filtered; the database also
uses a composite connector/owner foreign key to prevent cross-owner audit
records.

## Execution and security contract

- supported methods are `GET`, `HEAD`, `POST`, `PUT`, `PATCH`, and `DELETE`
- webhooks permit action `POST` only
- the requested path must remain inside an owner-approved prefix
- connector paths are canonical: queries, fragments, percent escapes,
  traversal segments, backslashes, and control characters are rejected
- request JSON is limited to 32 KiB and response JSON to 256 KiB
- timeouts are 1–10 seconds and retries are capped at two
- retries apply only to reads or requests carrying an explicit idempotency key
- redirects and environment-derived HTTP proxies are disabled
- bearer, OAuth bearer, and fixed `X-API-Key` authentication are supported
- credentials are XChaCha20-Poly1305 encrypted with a key outside PostgreSQL
- credentials are write-only, absent from responses, model prompts, and audit
  rows, and are deleted on revocation
- audits store method, path, timing, status, attempts, sizes, and content hashes;
  request/response bodies and credentials are not retained

The in-process rate limiter is a local deployment guard, not a distributed
quota service. A multi-instance deployment must place a shared rate-limit gate
in front of connector execution before relying on a global quota.

## Verified local workflow

`backend/scripts/real_connector_smoke.py` starts a real loopback HTTP service,
uses a disposable PostgreSQL database, provisions two independent owners, and
proves encrypted bearer authentication, health, owner isolation, path scopes,
an idempotent retry after HTTP 503, metadata-only audit history, log redaction,
credential destruction, and post-revocation denial. The release runtime gate
executes this smoke test.

## External boundaries

OAuth authorization-code exchange, consent screens, token refresh, provider
SDK semantics, provider-specific GraphQL schemas, email/calendar/CRM/social
accounts, and desktop/browser automation each require their legitimate service,
credentials, scopes, and (where applicable) an owner approval. The generic
connector runtime does not bypass MFA, OTP, billing, platform policy, or provider
authorization. A registry entry remains `external_dependency` until its real
provider workflow is configured and objectively verified.
