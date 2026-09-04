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

Connected-app credentials use a separate owner-only state root and encryption
key outside PostgreSQL. An operator must allowlist each exact credential-free
origin; non-loopback origins require HTTPS. The owner separately grants read or
write scope and one or more bounded path prefixes. Connector HTTP disables
redirects and environment proxy discovery, bounds JSON bodies and responses,
and retries only reads or explicitly idempotent actions. Revocation deletes the
credential ciphertext. Connector audit rows retain status, timing, sizes, and
content hashes—not headers, credentials, or payload bodies. Database composite
ownership constraints backstop API owner filtering.

Marketing campaign agents receive model inference only and cannot publish.
Owner-scoped composite foreign keys bind every campaign and publisher
connector to the same owner. Source facts are marked as untrusted data in the
agent instruction, every generated stage must match an independent verifier
digest, and publishing is a separate direct owner action after the persisted
`needs_approval` state. The connector audit retains only response hashes and
metadata. Analytics are validated and computed only after owner lookup, so a
foreign campaign identity cannot be used as a validation oracle.

Finance research agents likewise receive only model inference and treat every
owner-supplied market fact as untrusted data. Composite owner foreign keys bind
the complete finance graph. Prices, quantities, source references, histories,
fees, and risk limits are bounded in both API and PostgreSQL contracts. The
public finance API accepts only the literal `paper` execution mode and has no
broker endpoint. Unconfirmed or policy-exceeding simulations are persisted as
rejections; source-linked research is persisted only with verifier digest and
model evidence. Live broker access remains a separate external boundary.

Learning teacher agents receive only model inference. Programs, lessons,
activities, attempts, and review cards are bound by composite owner foreign
keys. Lesson text is persisted only with the independent Agent OS verifier's
matching SHA-256 and model evidence. Expected practice answers are stored as
normalized SHA-256 digests and are not returned by API contracts; incorrect
attempts do not reveal the explanation before success or exhaustion. Owner
memory may personalize a lesson only through an Agent OS request that disables
External AI selection. The selector fails closed if an admitted local route is
unavailable, even when a fallback provider is configured. Pronunciation scoring
remains a disabled external dependency, not an inferred score from transcription.

Creative experience agents likewise receive only model inference and disable
External AI fallback. Experience and turn rows use composite owner foreign
keys, and each persisted untouched response includes the independent Agent OS
verifier's matching SHA-256 plus model evidence. A Unicode-normalized fixed
gate rejects known explicit sexual, non-consensual, exploitative, grooming, and
minor-related sexual content before generation and before persistence. The
workspace remains general-audience only; video, animation, generative audio,
and protected adult operation are not promoted beyond explicit external
dependencies without separately verified runtime and policy controls.

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

Learning records are owner isolated by queries and composite foreign keys.
Document excerpts are bounded, restricted to ready documents owned by the
learner, delimited as untrusted data, and excluded when they contain recognized
credential forms. The local-only Teacher Agent has no tool or connector
permission. Learner answers are stored as SHA-256 digests; learning audit rows
store canonical metadata digests rather than answers, sources, prompts, model
output, or credentials. Missing grounded citations and credential-like
generated output fail closed before persistence.

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

Inside the networkless update container, browser chat uses a bounded
Ollama-compatible loopback fixture and a simulated hardware profile so the real
catalog, fail-closed admission, routing and generation API can be exercised
without mounting the host GPU or production model service. The fixture binds
only `127.0.0.1`, accepts three fixed protocol paths, has a 1 MiB request cap,
logs no prompts, and is explicitly excluded from benchmark and hardware-run
claims. Disposable hardware, external-provider and update state paths prevent
the gate from reading or mutating production state.

## Residual external risks

Owner devices and the Tailscale account must use strong OS authentication and
current security updates. Database backups and the workstation data directory
contain private content and require encrypted owner-controlled storage. Mobile
and desktop signing credentials must remain outside Git.

The 2026-09-03 dependency audit has zero high or critical npm advisories. It
reports 14 moderate affected nodes from two upstream chains: malformed-percent
decoding in `decode-uri-component`, reached through `query-string` and
`expo-router`, and a supplied-buffer bounds issue in `uuid`, reached through
Expo's build-time `xcode` tooling. Native Android validation and Expo Doctor
21/21 pass, but these remain upstream toolchain residuals. The audit's proposed
forced remediation would install breaking, SDK-incompatible Expo package
versions, so it is not applied without a compatible Expo release and the full
mobile release matrix.

This host's usable container boundary is the rootful Docker daemon; unprivileged
mount/network namespaces are restricted by the host AppArmor policy, so accepted
systemd user-unit namespace directives were not treated as evidence. WORK
STATION invokes Docker only with fixed content-addressed images and tightly
scoped mounts, and never passes the daemon socket into generated-code or update
candidate containers. Membership of the service account in the host `docker`
group is nevertheless a privileged administrative boundary. Moving these two
runners to a dedicated rootless daemon or separately confined service account
requires host-administrator credentials and is not claimed complete here.
