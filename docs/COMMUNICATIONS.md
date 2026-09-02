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
→ configured provider adapter
→ provider acceptance receipt
```

Without a legitimate configured provider, both submission endpoints fail
closed with HTTP 503. Video and live screen share remain disabled until a
separately verified WebRTC provider is configured. Account login, provider
credentials, MFA/OTP, billing, phone-number ownership, consent, and destination
legality remain external owner/provider responsibilities.

Destination and purpose values are not written to application logs or returned
in validation errors. A successful provider-adapter unit test proves only the
typed boundary; it is not evidence that a real external call ran.
