# Security model

## Trust boundaries

The FastAPI server is authoritative for identity and ownership. Clients never
supply a trusted user identity. Every conversation, message, asset, document,
memory, tool execution, and workflow lookup is filtered by the authenticated
owner. PostgreSQL, Ollama, ComfyUI, storage paths, and audio runtimes are not
public services.

Provisioning is a separate operator-only credential. It is accepted only by
`POST /api/v1/users`, compared by SHA-256 digest, and never shipped to a client.
User bearer tokens are stored only as SHA-256 digests in PostgreSQL. Each
device session has its own digest and revocation state; rotation invalidates
only the authenticated session's prior bearer. API token responses use
`no-store`.

## Client sessions

- web: current session storage, cleared on logout/authentication failure;
  transient network failure preserves a valid saved session
- desktop: OS credential vault through the fixed Tauri command surface
- mobile: Keychain/Keystore-backed Expo SecureStore

The backend permits at most 16 active owner-device sessions. Settings can name
the current device, issue a credential for another owner device, list safe
session metadata, and revoke a selected session. A newly issued token is
returned exactly once and is never returned by the list API. Logout attempts
server-side revocation and always removes the local session when secure storage
is available. Concurrent issuance is serialized by an owner-row lock, and
rotation uses an atomic digest compare-and-swap.

The legacy nullable digest column on `users` is retained only as a downgrade
bridge. Migration `0012_owner_device_sessions` moves existing active digests
into `user_sessions` and clears the legacy value. Authentication consults only
active `user_sessions`; downgrade never restores a revoked digest.

Tokens are never placed in URLs, source, bundles, notification previews, or
application logs. Tailscale identity headers are not a substitute for bearer
authorization.

Conversation search uses an authenticated bounded `POST` body so private title
or message search terms do not enter request URLs, browser history, or proxy
access logs. Results contain only owner-scoped conversation summaries; message
excerpts are not returned.

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
Conversation duplicate and edit/resend/regenerate actions create immutable,
owner-scoped forks. The server locks a bounded source snapshot, copies active
asset bytes under fresh generated identities with SHA-256 verification,
preserves owner-scoped citations, commits once, and removes copied files if the
transaction fails. Clients never alias or rewrite an existing message history.

Tool names, arguments, permissions, timeouts, result size, and workflow depth
come from a fixed server registry. Model output cannot select an arbitrary
shell command, URL, code evaluation target, or filesystem path. Clients have no
native shell/process/filesystem permission.

Web response Markdown is rendered as React elements without raw HTML. Raw tags
remain literal text, Markdown images are dropped, and model-authored links are
non-active text so a response cannot trigger browser network access. Copy is an
explicit owner action and copies only the selected persisted message.

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

Database integration and real runtime smoke runners fail before cleanup unless
the protected application and disposable test URLs identify different
databases. Runtime scripts then select only `127.0.0.1/ai_workspace_test` in
their own process; a username change alone cannot bypass the identity check.

## Residual external risks

Owner devices and the Tailscale account must use strong OS authentication and
current security updates. Database backups and the workstation data directory
contain private content and require encrypted owner-controlled storage. Mobile
and desktop signing credentials must remain outside Git.
