# Product capabilities and diagnostics

`GET /api/v1/ai/capabilities` is an authenticated, read-only snapshot used by
the web Settings & Diagnostics panel. It reports exactly eleven fixed product
capabilities as `available` or `unavailable`, with zero or more fixed blocking
reason codes. The response never contains configuration values, local paths,
runtime URLs, hardware identifiers, credentials, model references, private
content, or exception text.

The backend remains authoritative. Text chat and vision availability come from
the bounded allowlisted local model catalog and require a hardware-planned
`runnable_now` model; vision also requires configured owned-asset storage.
Attachments and document RAG require the same storage.
Personal memory, the strict tool registry, and bounded workflows are implemented
local product features. Model discovery begins only after the authentication
read transaction is rolled back.

Image generation, image editing, voice input, and voice output remain
`unavailable`. Their reason codes require implemented bounded loopback adapters
and explicitly allowlisted local models or voices; their asset pipelines also
report unconfigured private asset storage. GPU presence alone never
advertises a capability. See
[`media_runtime_prerequisites.md`](media_runtime_prerequisites.md) for the
workstation audit and operator prerequisites.

The browser validates the exact capability ID, status, blocker vocabulary,
count, uniqueness, and available/unavailable lifecycle before rendering it. It
maps reason codes to fixed local guidance and replaces fetch or decoding
failures with a generic error. Diagnostics load only when the explicit Settings
control opens and are aborted when the panel closes.
