# Long-term personal memory

Personal memory is distinct from Conversation history. The backend never
extracts, summarizes, or persists Messages automatically. A user must submit an
explicit authenticated `POST /api/v1/memories` request with one category and
one bounded memory entry.

Categories are `preference`, `fact`, `instruction`, and `project_context`.
Every row records the authenticated owner, `explicit_user_entry` provenance,
and creation/update timestamps. The API never returns owner IDs, embeddings,
or internal persistence metadata.

Owners can inspect active and forgotten entries with `GET /api/v1/memories`,
change retrieval with `GET`/`PUT /api/v1/memories/settings`, explicitly forget
an entry with `DELETE /api/v1/memories/{memory_id}`, and test bounded retrieval
with `GET /api/v1/memories/search`. Foreign IDs receive the same generic 404 as
missing IDs.

Forgetting is content erasure, not a UI-only flag. One token-guarded owner
update sets the content, 1,024-byte local embedding, and embedding norm to
`NULL` while setting `deleted_at`. A database CHECK constraint requires active
rows to have bounded nonblank content and a fixed-size positive embedding, and
requires deleted rows to contain neither. The content-free tombstone remains
inspectable so the user can see that the memory was forgotten.

Retrieval is local and deterministic. Memory retains its independent
256-dimensional feature-hash embedding and scores at most 500 active owner rows.
It selects at most eight entries within 4,000 characters. Instructions and
preferences are globally applicable; facts and project context must meet a
fixed relevance floor. A disabled owner setting prevents candidate
materialization entirely.

Selected memories are sent only to the configured local text runtime as a
separate SYSTEM background block. That block states that memory may be stale,
that current system and user instructions always override it, and that
unrelated memory must not be revealed. The current user Message remains later
in generation order. No memory content is copied into Message content, logs,
URLs, diagnostics, or frontend storage.

The web Memory control loads lazily after an authenticated user opens it. It
supports explicit creation, enable/disable, inspection, and forgetting, uses
fixed safe errors, aborts pending requests on close/unmount, renders all
content as text, and shows forgotten entries only as content-free tombstones.
