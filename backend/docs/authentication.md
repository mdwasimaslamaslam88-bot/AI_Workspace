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
Future owner-scoped Conversation and Message API operations must derive
`owner_id` from the authenticated current user rather than from client input.
