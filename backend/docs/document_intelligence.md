# Document intelligence

The implementation and operator contract are documented in
[`local_ai.md`](local_ai.md#local-document-intelligence-and-retrieval). The
feature is local-only: owned opaque Assets are safely parsed, deterministically
chunked, embedded by the explicitly allowlisted local Ollama embedding model,
owner-scoped at retrieval, injected as untrusted model context, and cited on
persisted assistant Messages. `nomic-embed-text:latest` is the validated
workstation model; embedding identity and dimensions are stored per chunk so a
future local model can be introduced without changing the API or schema.

Supported canonical media types are `text/plain`, `text/csv`,
`application/pdf`, and DOCX's standard OOXML media type. Upload the Asset with
the existing authenticated attachment endpoint, then ingest it with the
document endpoint. The web client performs that sequence automatically and
shows upload, indexing, ready, failure, and cancellation states.

The authenticated real-runtime release smoke is `scripts/real_rag_smoke.py`.
It is hard-restricted to `127.0.0.1/ai_workspace_test`, clears only that
disposable database, runs TXT upload through real Nomic embedding, verifies a
foreign owner receives no results, performs local generation with owned
citations, validates deletion tombstones and log redaction, and removes its
database and storage artifacts. Run it only after upgrading the disposable test
database to `head`, with `PYTHONPATH=.` from `backend/`.
