# Authentication and current-user identity

User provisioning is fail-closed and requires a dedicated operator credential.
Configure `USER_PROVISIONING_TOKEN_DIGEST` with exactly the 64-character
lowercase hexadecimal SHA-256 digest of a separate 43-character URL-safe opaque
token. Omission or a blank value disables provisioning; plaintext provisioning
credentials must not be placed in configuration or logs.

Call `POST /api/v1/users` with the operator credential in its dedicated header:

```http
X-User-Provisioning-Token: <opaque provisioning token>
```

The request body may be omitted or may be the strict empty object `{}`.
Identity, owner, token, digest, and unknown fields are rejected. Missing or
disabled configuration and missing, malformed, or incorrect provisioning
credentials all receive HTTP 403 with `User provisioning is not authorized`
before user-service construction or persistence.

The provisioning credential is independent from user bearer credentials: it
cannot authenticate ordinary bearer routes, and an ordinary bearer credential
cannot authorize provisioning. An authorized request preserves the existing
HTTP 201 response, returns the new user's opaque bearer credential once as
`access_token`, identifies its `token_type` as `bearer`, and includes
`Cache-Control: no-store`.

Clients must store that credential securely and send it through the standard
header on authenticated requests:

```http
Authorization: Bearer <access_token>
```

`GET /api/v1/users/me` resolves the current user from that header. Missing,
malformed, and unknown credentials all receive the same HTTP 401 response.
User UUIDs are public identifiers and are never accepted as proof of identity.

`GET /api/v1/users/{user_id}` is also authenticated and self-only. The path UUID
must equal the bearer credential's `current_user.id`; on a match, the endpoint
returns that already-loaded current user with the same `id`, `created_at`, and
`updated_at` response shape and performs no second user lookup. Every non-self
UUID, whether it belongs to another user or does not exist, receives the same
HTTP 404 response with `User not found`. Query and body identity fields cannot
override the authenticated current user.

The plaintext credential is not persisted. PostgreSQL stores only its SHA-256
digest in the nullable, unique `users.access_token_digest` column. Application
code must not log either the Authorization header or a plaintext credential.

An authenticated user may replace that credential with
`POST /api/v1/users/me/access-token/rotate`. The request body may be omitted or
may be the strict empty object `{}`; identity, token, digest, and unknown fields
are rejected. The server generates the replacement credential and atomically
updates its digest only when both the authenticated user ID and the digest that
authenticated the request still match. A successful HTTP 200 response contains
only:

```json
{
  "access_token": "<new opaque token>",
  "token_type": "bearer"
}
```

The response includes `Cache-Control: no-store`. After commit, the old
credential receives the existing uniform HTTP 401 and the replacement resolves
to the same user and owner-scoped data. Concurrent requests authenticated by
the same old credential have one winner; a request whose conditional update
loses returns HTTP 409 with `Access token rotation conflict` and no replacement
credential. Rotation does not add expiration, refresh tokens, multiple sessions,
logout, recovery, or account deletion.

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
An optional strict integer `seed` from 0 through 2,147,483,647 is accepted.
Explicit null, booleans, strings, floats, arrays, objects, non-finite values,
negative values, and values above that bound are rejected. Omitting `seed`
adds no seed override. A seed can support repeatability only with the same
model, context, runtime, and runtime version; it does not guarantee
cross-version determinism.
An optional finite numeric `top_p` from 0.0 through 1.0 is accepted, including
integer values 0 and 1. Explicit null, booleans, strings, arrays, objects,
non-finite numbers, negative values, and values above 1.0 are rejected.
Omitting `top_p` preserves the current runtime behavior without adding a
top-p override.
An optional strict integer `top_k` from 1 through 100 is accepted. Explicit
null, booleans, strings, floats, arrays, objects, non-finite values, zero,
negative values, and values above 100 are rejected. Omitting `top_k` preserves
the current runtime behavior without adding a top-k override.
An optional finite numeric `min_p` from 0.0 through 1.0 is accepted, including
integer values 0 and 1. Explicit null, booleans, strings, arrays, objects,
non-finite numbers, negative values, and values above 1.0 are rejected.
Omitting `min_p` preserves the current runtime behavior without adding a
min-p override.
An optional finite numeric `repeat_penalty` from 0.5 through 2.0 is accepted,
including integer values 1 and 2. Explicit null, booleans, strings, arrays,
objects, non-finite numbers, values below 0.5, and values above 2.0 are
rejected. Omitting `repeat_penalty` preserves the current runtime behavior
without adding a repetition-penalty override.
An optional strict integer `repeat_last_n` from 0 through 2,048 is accepted.
Zero explicitly disables repetition lookback, while positive values select a
bounded token-history window. Explicit null, booleans, strings, floats, arrays,
objects, non-finite values, negative values including Ollama's `-1` sentinel,
and values above 2,048 are rejected. Omitting `repeat_last_n` preserves the
current runtime behavior without adding a repetition-window override.
An optional finite numeric `typical_p` from 0.0 through 1.0 is accepted,
including integer values 0 and 1. Explicit null, booleans, strings, arrays,
objects, non-finite numbers, negative values, and values above 1.0 are rejected.
Omitting `typical_p` preserves the current runtime behavior without adding a
locally typical sampling override.
An optional finite numeric `presence_penalty` from -2.0 through 2.0 is
accepted, including integer values within that range. Positive values
discourage reuse of tokens already present, negative values encourage reuse,
and zero applies no presence penalty. Explicit null, booleans, strings, arrays,
objects, non-finite numbers, and out-of-range values are rejected. Omitting
`presence_penalty` adds no presence-penalty override.
An optional finite numeric `frequency_penalty` from -2.0 through 2.0 is
accepted, including integer values within that range. Positive values
discourage tokens proportionally to their prior frequency, negative values
encourage reuse, and zero applies no frequency penalty. Explicit null,
booleans, strings, arrays, objects, non-finite numbers, and out-of-range values
are rejected. Omitting `frequency_penalty` adds no frequency-penalty override.
An optional `stop_sequences` JSON array containing one through four strict
strings is accepted. Each sequence must contain one through 128 Unicode
characters. Exact content and order are preserved without trimming or
normalization; non-empty whitespace and control text, including newline
sequences, are valid, and duplicates remain unchanged. Explicit null, scalar
values, objects, an empty array, more than four entries, non-string entries,
empty strings, and strings longer than 128 characters are rejected. Omission is
the only way to preserve model and runtime stop defaults.

When `user_message` is supplied, the exact content is committed first as a
server-assigned user Message before generation. Omission or null preserves
generation-only behavior. User IDs, owner IDs, roles, sequences, raw runtime
model references, client-supplied Message arrays, raw `stop`, arbitrary
generation options, Ollama's `-1` repetition-window sentinel, context controls,
other sampling controls, and streaming flags are rejected.

Missing and foreign-owned Conversations return the same generic HTTP 404.
Authentication completes before Conversation lookup, model discovery, or
generation. The read transaction is released before local inference, and the
assistant append uses the existing owner-scoped atomic sequence allocation
with an expected-sequence condition. If the Conversation changes while
generation is running, the stale output is not persisted and the request
returns HTTP 409.

After catalog resolution, Conversation generation requires the selected model
to advertise the normalized `text_generation` capability obtained from
Ollama's documented model-detail capability list. A model without that
capability returns HTTP 409 with `Model does not support text generation` before
runtime dispatch or assistant persistence. If the request already committed an
optional `user_message`, that Message remains available for a generation-only
retry with an eligible model.

For a request containing `user_message`, the captured generation context must
end at exactly that newly committed user Message. If another Message is
committed first, generation does not start and the request returns HTTP 409.
Failures after the user Message commit leave that Message available for a
generation-only retry and do not persist an assistant Message.

Generation admission is keyed by the authenticated current user's stable UUID,
not by bearer-token text, so access-token rotation cannot create a second
permit. Conversation ownership is confirmed before the process-local controller
attempts fail-fast admission. At most one generation may be active per user,
including across different owned Conversations, and different users share the
`GENERATION_MAX_ACTIVE_PER_PROCESS` bound. This strict integer defaults to 1
and accepts only 1 through 8 for a single application process.

A request denied by either admission rule returns HTTP 429 with `Generation
capacity is busy` in the existing safe error envelope. It does not persist an
optional `user_message`, query generation context, discover a model, invoke
Ollama, or append an assistant Message. No counter, user identity, or configured
capacity is returned. Admitted permits cover the optional USER commit through
the guarded assistant commit and are released on success, failure, stale-output
rejection, and cancellation. Admission does not wait or create a queue and does
not use Redis or PostgreSQL locks.
