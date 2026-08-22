# Bounded local workflows

The workflow API schedules small, explicit sequences from the fixed local tool
registry. It is an orchestration layer over the same server-authorized tools;
it is not a general-purpose agent, planner, code runner, shell, filesystem
browser, or network client.

Authenticated owners can:

- create one to eight validated steps with `POST /api/v1/workflows`;
- start a pending workflow with `POST /api/v1/workflows/{id}/start`;
- inspect one workflow or at most 50 recent workflows with authenticated GETs;
- cancel pending or running work with `DELETE /api/v1/workflows/{id}`.

Each definition is validated against the current tool registry before it is
persisted. The server captures the registry-assigned tool name and permission,
canonical bounded arguments, and deterministic position. The executor checks
the registry and permission again immediately before every step. Client input
cannot supply or override a permission, deadline, output cap, lifecycle state,
or execution identity.

## Execution bounds and state

A workflow has at most eight sequential steps, a 60-second wall-clock deadline,
a 10-second per-step ceiling, and process-local admission of two active
workflows. The called tool retains its own tighter deadline and output bound.
Workflow result JSON is capped at 65,536 characters and each copied step result
at 16,384 characters. A result that cannot fit fails closed instead of being
truncated into misleading success.

The durable workflow and every durable step use only `pending`, `running`,
`completed`, `failed`, `cancelled`, or `timed_out`. Conditional owner/status
updates claim the workflow and each step exactly once. A step failure stops the
sequence and marks unrun steps cancelled. Cancellation stops the local task and
records a deterministic terminal state. Startup reconciliation marks work
interrupted by process exit as failed; shutdown cancels and awaits all active
tasks before database disposal.

Every repository read and write includes the authenticated owner. Foreign and
missing identities receive the same 404. Tool execution audit rows link back to
completed workflow steps where applicable, while workflow history exposes no
owner ID or internal exception detail. A composite database foreign key forces
every step owner to equal its parent workflow owner, even for bypass writes.
The linked tool audit is marked with the server-owned `workflow` initiator.

## Transaction and UI behavior

Creation and every lifecycle transition use short explicit transactions. The
runner copies immutable primitive step data and rolls the claim transaction
back before invoking the tool service, so no ORM object or database transaction
is held across tool execution. Each tool call keeps its existing owner checks,
audit record, timeout, cancellation, and output policy.
Start and cancellation endpoints also roll back the authenticated user lookup
before calling the process-lifetime runner.

The Workflows panel offers a fixed owner-research composition using document,
memory, and Conversation search. It shows persisted task progress, current
step, tool permission/activity, bounded results, safe error codes, and cancel
controls. It polls only while a task is running and aborts outstanding requests
when the panel unmounts. It does not accept free-form tool JSON or invent a
terminal result when progress cannot be confirmed.
