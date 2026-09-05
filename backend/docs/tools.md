# Bounded local tools

The authenticated tools API exposes a fixed server-owned registry. `GET
/api/v1/tools` returns each tool's JSON Schema, permission, deadline, and output
cap. `POST /api/v1/tools/{name}/executions` performs one explicit call, and
`GET /api/v1/tools/executions` lists at most 100 recent records for the current
owner. The web Tools panel uses these endpoints and never offers free-form JSON,
code, shell, or network execution. Filesystem operations are exposed only when
an operator configures one to four narrow roots with `FILESYSTEM_TOOL_ROOTS`.
Each authenticated owner is isolated in a UUID-named child of those roots.

The initial registry contains only:

- `calculator`: bounded arithmetic parsed with Python's AST and evaluated by an
  operator allowlist; names, calls, attributes, collections, and excessive
  exponentiation are rejected.
- `local_time`: reads an installed IANA timezone using the standard timezone
  database.
- `document_search`: reuses ready document-index retrieval for the authenticated
  owner.
- `conversation_search`: joins every matching message through a Conversation
  owned by the authenticated user and optionally narrows to an owned
  conversation.
- `memory_search`: reuses enabled, active personal-memory retrieval for the
  authenticated owner.
- `filesystem.write`: creates one bounded UTF-8 file without overwriting a
  different existing file.
- `filesystem.read`, `filesystem.exists`, `filesystem.list`, and
  `filesystem.stat`: inspect bounded non-sensitive content and metadata under
  the same owner workspace.

Filesystem paths are relative only. Absolute paths, traversal, hidden or
credential-shaped names, protected suffixes, symlinks, non-regular files,
oversized content, and credential-shaped content fail closed. Descriptor-
relative operations use no-follow semantics so a path cannot escape during
resolution. Writes are atomic and same-content repeats are idempotent.

Pydantic rejects unknown or coercion-prone bounded fields before an execution
record is created. The registry, not the browser or model, assigns permission,
deadline, and output size. Every accepted call first commits a `running`
`tool_executions` row, performs no database-external work while holding that
transaction, then conditionally writes one terminal state. Disconnects become
`cancelled`, deadlines become `timed_out`, safe runtime errors become `failed`,
and successful bounded JSON becomes `completed`. Startup reconciliation marks
calls interrupted by process exit as `failed` with `server_restarted`.

Execution history contains owner ID internally but never returns it. Optional
Conversation context is verified against the same owner before audit creation.
Arguments and results are canonical bounded JSON text; internal exceptions and
raw database details are never returned or logged. Audit metadata distinguishes
direct `explicit_user` calls from bounded `workflow`, `chat_model`, and
`chat_verifier` calls. Workflow definitions never admit filesystem tools.
Write content is replaced in audit arguments by its byte count and SHA-256;
read content is available to the authorized immediate caller but redacted from
durable audit history.

Authenticated conversation generation performs intent-based tool selection.
It supplies only relevant server-owned schemas to a tool-capable local model,
validates the owner, capability, permission, and path again before execution,
executes through this same `ToolService`, and returns the real structured
result to the model for continuation. A successful filesystem write is not
reported complete until separate `filesystem.exists` and `filesystem.read`
executions exact-match the requested content. The response includes a bounded
execution trace for the UI. Missing capabilities, denied calls, tool failures,
and verification mismatches return truthful `BLOCKED` or `FAILED` states.
