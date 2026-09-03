# Realtime communications boundary

`GET /api/v1/communications/capabilities` is the authenticated authority for
phone-call, callback, video, and screen-share provider readiness. These remain
`external_dependency` capabilities. The product does not claim that a call was
placed merely because a UI action was requested.

Phone-call and callback requests use a typed provider-neutral contract:

```text
owner session
→ explicit owner approval
→ bounded E.164 destination and purpose
→ configured provider adapter or health-verified owner connector
→ provider acceptance receipt
→ connector audit evidence where the connector route is used
```

The production connector route uses the existing encrypted, owner-scoped
connector platform. A connector is admitted for phone calls or callbacks only
when it is enabled, most recently health-verified, has `write` scope, and
advertises the corresponding `phone_call` or `callback` capability. Requests
use the fixed paths `/communications/phone-calls` and
`/communications/callbacks`; the connector's exact origin and path allowlist
must authorize those paths. The provider must return the matching `request_id`
and the exact state `accepted_by_provider`. A mismatched or malformed receipt
fails closed and the UI does not claim success.

The desktop/web Calls panel and the Android Calls route expose provider state,
explicit owner approval, eligible gateway selection, failure state, and the
verified request/audit receipt. Provider credentials remain write-only in the
Connections management surface.

Without a legitimate configured provider, both submission endpoints fail
closed with HTTP 503. Video and live screen share remain disabled until a
separately verified WebRTC provider is configured. Account login, provider
credentials, MFA/OTP, billing, phone-number ownership, consent, and destination
legality remain external owner/provider responsibilities.

Destination and purpose values are not written to application logs or returned
in validation errors. Unit tests and the loopback connector E2E prove the local
gateway contract, receipt verification, encrypted authentication, owner
isolation, and audit trail. They are not evidence that a real carrier call ran.
