# Security model

## Trust boundaries

The FastAPI server is authoritative for identity and ownership. Clients never
supply a trusted user identity. Every conversation, message, asset, document,
memory, tool execution, and workflow lookup is filtered by the authenticated
owner. PostgreSQL, Ollama, ComfyUI, storage paths, and audio runtimes are not
public services.

Provisioning is a separate operator-only credential. It is accepted only by
`POST /api/v1/users`, compared by SHA-256 digest, and never shipped to a client.
User bearer tokens are stored only as SHA-256 digests in PostgreSQL. Token
rotation invalidates the prior bearer. API token responses use `no-store`.

## Client sessions

- web: current session storage, cleared on logout/authentication failure;
  transient network failure preserves a valid saved session
- desktop: OS credential vault through the fixed Tauri command surface
- mobile: Keychain/Keystore-backed Expo SecureStore

Owner-initiated rotation is deliberately global because the backend maintains
one active bearer digest for the owner. It invalidates other connected devices;
those devices must reconnect with the replacement credential. Clients do not
attempt to display, synchronize, or provision credentials.

Tokens are never placed in URLs, source, bundles, notification previews, or
application logs. Tailscale identity headers are not a substitute for bearer
authorization.

## Edge controls

- exact CORS origin list; non-loopback entries require HTTPS
- no wildcard credentialed origin, method, or request-header policy
- fixed Content Security Policy, frame denial, `nosniff`, no-referrer,
  Permissions Policy, same-origin opener policy, and HTTPS-only HSTS
- raw request body maximum of 1 MiB and lower configurable default
- bounded generation, ingestion, image, voice, database pool, timeout, and
  concurrency controls
- per-process fixed-window provisioning and repeated-auth-failure throttling
  keyed only to the ASGI peer; forwarded client headers are not trusted
- generic, request-ID-bearing error responses without exception detail

Tailscale Serve supplies private tailnet routing and TLS. Funnel and router port
forwarding are not used.

## Content and execution

Uploads are size/type bounded, stored under server-generated identities, and
downloaded only through authenticated owner checks. Filesystem paths never
appear in responses. Private media uses `no-store`, safe MIME metadata,
`nosniff`, attachment disposition, and bounded/non-supported range behavior.

Tool names, arguments, permissions, timeouts, result size, and workflow depth
come from a fixed server registry. Model output cannot select an arbitrary
shell command, URL, code evaluation target, or filesystem path. Clients have no
native shell/process/filesystem permission.

## Audit commands

```bash
./scripts/security_audit.sh
git diff --check
git grep -nE 'Authorization|X-User-Provisioning-Token' -- ':!backend/tests/*'
```

The scripted audit checks tracked credential signatures, client builds for
operator/path leakage, desktop CSP scope, Python dependencies, and npm
high/critical advisories. Manual review must still inspect authorization,
transactions, upload parsing, lifecycle cleanup, and exact staged changes.

## Residual external risks

Owner devices and the Tailscale account must use strong OS authentication and
current security updates. Database backups and the workstation data directory
contain private content and require encrypted owner-controlled storage. Mobile
and desktop signing credentials must remain outside Git.
