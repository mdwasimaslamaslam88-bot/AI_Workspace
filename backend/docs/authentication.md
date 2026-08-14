# Authentication and current-user identity

The API provisions an opaque bearer credential together with each user. Call
`POST /api/v1/users` without an identity, owner, or credential field. A
successful response returns the credential once as `access_token`, identifies
its `token_type` as `bearer`, and includes `Cache-Control: no-store`.

Clients must store that credential securely and send it through the standard
header on authenticated requests:

```http
Authorization: Bearer <access_token>
```

`GET /api/v1/users/me` resolves the current user from that header. Missing,
malformed, and unknown credentials all receive the same HTTP 401 response.
User UUIDs are public identifiers and are never accepted as proof of identity.

The plaintext credential is not persisted. PostgreSQL stores only its SHA-256
digest in the nullable, unique `users.access_token_digest` column. Application
code must not log either the Authorization header or a plaintext credential.
Owner-scoped Conversation and Message API operations derive `owner_id` from the
authenticated current user rather than from client input. In particular,
`POST /api/v1/conversations` creates a conversation and its initial user message
atomically for the bearer credential's current user. The request may also
include an optional nonblank `system_prompt`. The required `initial_message`
must contain at least one non-whitespace character. Validation neither trims
its leading or trailing whitespace nor adds a length limit. The server assigns
system prompt content the system role at sequence 1 and the initial user
Message sequence 2. When the system prompt is omitted or null, the initial user
Message remains sequence 1. The Conversation and all bootstrap Messages are
committed once as one transaction, and the response shape remains unchanged.
Clients still cannot supply Message roles, sequences, Conversation IDs, or
owner identity.

`GET /api/v1/conversations` returns only the bearer credential's current user's
conversations, ordered by `updated_at` descending and then conversation `id`
descending. `limit` defaults to 50 and is bounded from 1 to 100. Subsequent
pages must provide both `cursor_updated_at` and `cursor_id` from the previous
response's composite `next_cursor`; the cursor fields are not identity inputs.
Terminal and empty pages return `next_cursor` as `null`.

`GET /api/v1/conversations/{conversation_id}` returns the requested conversation
only when it belongs to the bearer credential's current user. The response is
limited to the conversation's `id`, nullable `title`, `created_at`, and
`updated_at`. Missing and foreign-owned conversations receive the same generic
HTTP 404 response, so the endpoint does not disclose another user's ownership.

`PATCH /api/v1/conversations/{conversation_id}` renames a conversation only
when it belongs to the bearer credential's current user. The request must
include `title`; explicit `null` clears the title, while a string is limited to
255 characters and must contain at least one non-whitespace character. Leading
and trailing whitespace is preserved. Undeclared fields are rejected, and
missing and foreign-owned conversations receive the same generic HTTP 404
response.

`DELETE /api/v1/conversations/{conversation_id}` deletes a conversation owned
by the bearer credential's current user and returns HTTP 204 with an empty
response body. Associated messages are removed by the existing database
cascade. Missing and foreign-owned conversations receive the same generic HTTP
404 response.

`POST /api/v1/conversations/{conversation_id}/messages` appends a user message
only when the bearer credential's current user owns the conversation. The API
requires `content` containing at least one non-whitespace character and
preserves its exact leading and trailing whitespace without adding a length
limit. The API always supplies the user role and the database allocates the
sequence number; clients cannot supply identity, role, conversation, or
sequence fields in the request body. Missing and foreign-owned conversations
receive the same generic HTTP 404 response.

`GET /api/v1/conversations/{conversation_id}/messages` returns only messages
from a conversation owned by the bearer credential's current user, ordered by
ascending message sequence. `limit` defaults to 50 and is bounded from 1 to
100. An optional positive integer `cursor` is the last sequence number returned
by the previous page. The response provides that sequence number as
`next_cursor` only when another page exists. Empty, missing, and foreign-owned
conversations all return the same empty page without disclosing ownership.

POST /api/v1/conversations/{conversation_id}/messages/generate resolves the
current owner exclusively from the bearer credential and generates one
non-streaming local assistant Message from that owner's existing Conversation
history. The request requires an opaque public model_id and may include one
optional nonblank `user_message`. It may also include a strict integer
`max_output_tokens` from 1 through 1,024; omission retains the 1,024-token
default. An optional finite numeric `temperature` from 0.0 through 2.0 is
accepted, including integer values in that range. Explicit null, booleans,
strings, non-finite numbers, and out-of-range values are rejected. Omission
preserves the current runtime behavior without adding a temperature override.
When `user_message` is supplied, the exact content is committed first as a
server-assigned user Message before generation. Omission or null preserves
generation-only behavior. User IDs, owner IDs, roles, sequences, raw runtime
model references, client-supplied Message arrays, arbitrary generation options,
other sampling controls, and streaming flags are rejected.

Missing and foreign-owned Conversations return the same generic HTTP 404.
Authentication completes before Conversation lookup, model discovery, or
generation. The read transaction is released before local inference, and the
assistant append uses the existing owner-scoped atomic sequence allocation
with an expected-sequence condition. If the Conversation changes while
generation is running, the stale output is not persisted and the request
returns HTTP 409.

For a request containing `user_message`, the captured generation context must
end at exactly that newly committed user Message. If another Message is
committed first, generation does not start and the request returns HTTP 409.
Failures after the user Message commit leave that Message available for a
generation-only retry and do not persist an assistant Message.
