# CRM, Social/CMS, and Marketing Provider Status

Evidence date: 2026-09-04

| Provider family | Local state | External state | Production connector | External evidence |
| --- | --- | --- | ---: | --- |
| CRM | RUNTIME_READY | EXTERNAL_BLOCKED | 0 | None; no third-party CRM request ran |
| Social/CMS | RUNTIME_READY | EXTERNAL_BLOCKED | 0 | None; no content was published externally |
| Marketing | RUNTIME_READY | EXTERNAL_BLOCKED | 0 | None; no campaign/provider analytics/spend ran |

The local runtime uses the production connector code against a real loopback
HTTP server and disposable PostgreSQL. It proves the protocol, security gates,
metadata-only audits, result validation, and revocation behavior; it is not
third-party-provider-live evidence.

CRM coverage includes account identity, contact create/read/update, note/task/
deal create, owner isolation, and revocation. Social/CMS coverage includes
account/site identity and reversible draft create/read/update/unpublish.
Marketing coverage includes the local research-to-optimization pipeline,
explicit approval, an exact `campaign.publish` capability, verified semantic
publish receipt, and fail-closed handling of an unverified HTTP-success body.

Activation requires a separately authorized owner account, legitimate OAuth or
API credential, exact HTTPS API/token origins, minimum scopes and paths, any
provider review/MFA/billing, and a bounded reversible destination. Publishing
and paid campaign activation require explicit content/destination approval;
spend requires separate owner authorization and limits.
