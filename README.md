# AI Workspace

A local-first Personal AI modular monolith with a FastAPI/PostgreSQL backend and
a React/TypeScript client. The authenticated workspace integrates text chat,
vision-capable owned attachments, TXT/CSV/PDF/DOCX intelligence with local RAG
and citations, explicit long-term memory, a fixed safe tool registry, bounded
workflows, local image generation/editing, local speech recognition/synthesis,
model selection, settings, and truthful capability diagnostics.

Every media feature remains fail-closed unless its private storage, bounded
adapter, exact local model or voice, and current hardware admission are all
available. The unified UI exposes only the controls the authenticated backend
can execute locally; it never substitutes a cloud API or mocked runtime.

See [frontend/README.md](frontend/README.md) for browser setup and
[backend/docs/local_ai.md](backend/docs/local_ai.md) for backend architecture,
security boundaries, runtime configuration, and validation.
