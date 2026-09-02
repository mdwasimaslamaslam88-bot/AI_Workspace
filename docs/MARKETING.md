# Verified business and marketing workflows

AI OS provides an authenticated, owner-scoped campaign workflow at
`/api/v1/marketing/campaigns`. It composes the existing local-first Agent OS
and connector boundary; it does not create a second model router or grant a
marketing model direct network or tool privileges.

## Implemented lifecycle

```text
owner campaign and cited source facts
→ research
→ strategy
→ content
→ creative brief
→ NEEDS APPROVAL
→ owner-authorized connector publish
→ submitted source analytics
→ deterministic optimization guidance
```

The first four stages use the existing research/planner specialists with only
`model_inference`. Each untouched result must have passed the independent Agent
OS verifier; AI OS persists the output, verifier SHA-256, model identity,
duration, and real stage state. The strategy instruction covers channel,
campaign, SEO, lead, CRM, sales, and customer-support handoffs where relevant
to the owner's objective. Generated facts must be grounded in the supplied
references. Source text is explicitly treated as untrusted data, not agent
instructions.

Campaigns are bounded to 50 per owner, eight fixed stages, 16 source facts,
32,768 characters per generated stage, one active campaign per process, a
ten-minute wall deadline, and Agent OS's bounded retry policy. Startup recovery
terminalizes interrupted generation or publishing rather than claiming an
unknown action succeeded.

## Publishing and approval

Publishing is impossible until all generated stages finish and the persisted
state is `needs_approval`. The owner then invokes the approval endpoint. A
campaign must reference an enabled connector owned by the same owner, with
`write` scope and an exact authorized path. The composite database foreign key
prevents cross-owner publisher references.

The publish action uses a stable idempotency key and the connector runtime's
origin allowlist, path canonicalization, timeout, retry, rate, credential, and
audit policies. Campaign history stores only the connector execution ID,
status, timing, and response hash evidence—not provider response bodies or
credentials. A connector/provider failure becomes a terminal campaign failure;
it is never shown as a successful publish.

Draft-only campaigns are supported. They stop at `needs_approval` and display
the publisher as an external boundary. Email, social, CRM, SEO, analytics, and
other provider-specific actions remain `external_dependency` until a legitimate
service, owner authorization, scopes, and provider semantics are configured and
verified. AI OS does not bypass OAuth, MFA, OTP, billing, platform policy, or
publishing consent.

## Grounded analytics

Analytics are accepted only after a verified publish. The owner submits a
source reference, timezone-aware observation time, and nonnegative integer
impressions, clicks, conversions, spend, and revenue. AI OS rejects impossible
funnels such as clicks exceeding impressions. CTR, conversion rate, cost per
conversion, and return on ad spend are calculated deterministically with fixed
decimal rounding. Optimization suggestions are deterministic rules derived
only from those submitted metrics; they are not fabricated provider data and
contain no claim of guaranteed results or profit.

## Product surfaces and verification

The web/desktop Marketing panel and mobile Studio expose campaign creation,
real stage activity, cancellation, approval, publisher boundaries, and
analytics submission. They poll persisted server state and never synthesize a
progress percentage.

Automated evidence covers the service calculations, verifier digest contract,
authenticated API, response validation, web panel, mobile API, exact migration
parity, owner isolation, restart reconciliation, connector permission, a real
loopback publish request, metadata-only audit linkage, and grounded analytics.
