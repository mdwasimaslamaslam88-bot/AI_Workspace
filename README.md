# AI Workspace

A local-first Personal AI modular monolith with a FastAPI/PostgreSQL backend and
a React/TypeScript client. The authenticated workspace integrates text chat,
vision-capable owned attachments, TXT/CSV/PDF/DOCX intelligence with local RAG
and citations, explicit long-term memory, a fixed safe tool registry, bounded
workflows, model selection, settings, and truthful capability diagnostics.

Image generation, image editing, and voice remain fail-closed because this
workstation has no implemented bounded local adapters or allowlisted media
models. The UI advertises no execution control for them and reports their exact
operator prerequisites instead of mocking availability.

See [frontend/README.md](frontend/README.md) for browser setup and
[backend/docs/local_ai.md](backend/docs/local_ai.md) for backend architecture,
security boundaries, runtime configuration, and validation.
