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

Agent OS plans use fixed typed permission profiles. The authenticated generic
agent endpoint grants model inference only; model text cannot grant workspace,
terminal, browser, network or tool authority. Executable code verification uses
server-owned profiles and a pinned Docker image with no network/capabilities,
a read-only container root, bounded ephemeral storage, memory/CPU/process
limits, no owner home or host sockets, and immutable original-artifact hashes.
The verifier never repairs code before scoring. If the exact local image or
Docker execution boundary is unavailable, verification fails closed.

External provider keys are write-only and XChaCha20-Poly1305 encrypted under an
owner-only state root. Provider HTTP origins are fixed, redirects and proxy
environment variables are disabled, and keys never enter prompts. External
model admission requires a content-addressed registered complete-category
benchmark record that exactly matches policy metrics.

The process retains at most 512 metadata-only containment events for repeated
authentication failure, rate limiting, oversized bodies and unexpected error
containment. Authenticated diagnostics return only event kind and timestamp;
they never retain peer address, URL, headers, body or owner content.

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

Self-update candidates run outside source and cannot activate until all fixed
release/security/compatibility/rollback gates pass. Checkpoints include an
integrity-verified Git bundle and encrypted configuration. Activation remains an
explicit owner decision; health degradation can atomically restore the previous
managed release. This is defense in depth, not a claim of being unhackable.

Database integration and real runtime gates never migrate or truncate the
application database. The normal release path initializes a randomly named,
mode-700 PostgreSQL cluster in the system temporary directory, binds it only to
a random loopback port, uses a transient SCRAM password that is neither printed
nor written to disk in plaintext, and removes only that validated temporary
root after shutdown.
The protected application environment is loaded before the child test URL is
substituted. The approved `127.0.0.1/ai_workspace_test` target and distinct
application/test identity checks remain enforced inside the Python harness.
An isolated self-update candidate intentionally has no private application
environment; its fixed container contract supplies a non-routable loopback
identity sentinel solely for that distinctness check. Alembic upgrade and
schema-drift checks still execute against the random disposable database, and
the sentinel endpoint is never contacted.

The real-browser gate uses an independent random provisioning credential whose
SHA-256 digest is supplied only to its disposable backend. The plaintext value
and returned bearer remain in process memory; the provisioning value reaches
the browser runner through an anonymous pipe rather than arguments or its
environment. Neither value enters URLs, logs, screenshots, traces, video,
source, or the application database. The
browser asserts that the bearer is absent from page text, local storage, and
resource URLs; it also inspects Cache Storage to prove no `/api/` response was
persisted. Logout revokes the temporary device session before the browser and
database are removed.

## Residual external risks

Owner devices and the Tailscale account must use strong OS authentication and
current security updates. Database backups and the workstation data directory
contain private content and require encrypted owner-controlled storage. Mobile
and desktop signing credentials must remain outside Git.

The 2026-08-31 dependency audit has zero high or critical npm advisories. It
does report one moderate root advisory in `uuid@7.0.3` (buffer bounds when a
caller supplies a buffer), reached through Expo's build-time `xcode` tool and
expanded by npm into 11 affected dependency nodes. The application does not
invoke that UUID buffer API, and native Android validation passes, but this is
still retained as an upstream build-tool residual. A forced transitive override
is not used because the current Expo dependency graph does not resolve it and a
breaking framework change would need the full mobile release matrix.

This host's usable container boundary is the rootful Docker daemon; unprivileged
mount/network namespaces are restricted by the host AppArmor policy, so accepted
systemd user-unit namespace directives were not treated as evidence. WORK
STATION invokes Docker only with fixed content-addressed images and tightly
scoped mounts, and never passes the daemon socket into generated-code or update
candidate containers. Membership of the service account in the host `docker`
group is nevertheless a privileged administrative boundary. Moving these two
runners to a dedicated rootless daemon or separately confined service account
requires host-administrator credentials and is not claimed complete here.
