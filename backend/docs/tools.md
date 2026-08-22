# Bounded local tools

The authenticated tools API exposes a fixed server-owned registry. `GET
/api/v1/tools` returns each tool's JSON Schema, permission, deadline, and output
cap. `POST /api/v1/tools/{name}/executions` performs one explicit call, and
`GET /api/v1/tools/executions` lists at most 100 recent records for the current
owner. The web Tools panel uses these endpoints and never offers free-form JSON,
code, shell, filesystem, or network execution.

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
direct `explicit_user` calls from calls executed by a bounded `workflow`.
