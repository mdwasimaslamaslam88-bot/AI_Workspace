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
atomically for the bearer credential's current user.

`GET /api/v1/conversations` returns only the bearer credential's current user's
conversations, ordered by `updated_at` descending and then conversation `id`
descending. `limit` defaults to 50 and is bounded from 1 to 100. Subsequent
pages must provide both `cursor_updated_at` and `cursor_id` from the previous
response's composite `next_cursor`; the cursor fields are not identity inputs.
Terminal and empty pages return `next_cursor` as `null`.

`POST /api/v1/conversations/{conversation_id}/messages` appends a user message
only when the bearer credential's current user owns the conversation. The API
always supplies the user role and the database allocates the sequence number;
clients cannot supply identity, role, conversation, or sequence fields in the
request body. Missing and foreign-owned conversations receive the same generic
HTTP 404 response.

`GET /api/v1/conversations/{conversation_id}/messages` returns only messages
from a conversation owned by the bearer credential's current user, ordered by
ascending message sequence. `limit` defaults to 50 and is bounded from 1 to
100. An optional positive integer `cursor` is the last sequence number returned
by the previous page. The response provides that sequence number as
`next_cursor` only when another page exists. Empty, missing, and foreign-owned
conversations all return the same empty page without disclosing ownership.
