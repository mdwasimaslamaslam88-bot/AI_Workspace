# Document intelligence

The implementation and operator contract are documented in
[`local_ai.md`](local_ai.md#local-document-intelligence-and-retrieval). The
feature is local-only: owned opaque Assets are safely parsed, deterministically
chunked and embedded, owner-scoped at retrieval, injected as untrusted model
context, and cited on persisted assistant Messages.

Supported canonical media types are `text/plain`, `text/csv`,
`application/pdf`, and DOCX's standard OOXML media type. Upload the Asset with
the existing authenticated attachment endpoint, then ingest it with the
document endpoint. The web client performs that sequence automatically and
shows upload, indexing, ready, failure, and cancellation states.
