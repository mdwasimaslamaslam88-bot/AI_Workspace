# AI Workspace frontend

A local React, TypeScript, and Vite client for the existing AI Workspace
FastAPI backend. The frontend has no server-side component, proxy, analytics,
cloud provider, or provisioning flow.

## Prerequisites

Run the configured backend on `http://127.0.0.1:8000` and provision a user
through the backend's operator-controlled process. The browser requires the
resulting user bearer token; it must never receive the provisioning token or
`USER_PROVISIONING_TOKEN_DIGEST`.

The backend must allow `http://localhost:3000` (the existing default) or the
origin from which this frontend is served.

## Configure and run

```bash
cp .env.example .env
npm install
npm run dev
```

Open `http://localhost:3000`. If the backend uses another local origin, set
only the public origin in `.env`:

```dotenv
VITE_API_BASE_URL=http://127.0.0.1:8000
```

Frontend environment variables are bundled into browser code and must never
contain credentials. `VITE_API_BASE_URL` accepts an HTTP or HTTPS origin with
no embedded credentials, path, query, or fragment.

## Authentication

Paste an already-provisioned user bearer token into the connect screen. The
token is held in browser `sessionStorage`, sent only in the
`Authorization: Bearer` header to the configured backend, and removed on
logout or HTTP 401. Closing the browser session also removes it. It is never
placed in a URL, page text, diagnostic, or application log.

Settings can rotate the owner bearer token. Rotation invalidates the prior
token globally and replaces the current client session atomically where the
platform storage operation succeeds; reconnect other owner devices afterward.

## Current workflow

The local client supports:

- current-user validation and capability-aware local model selection;
- real backend Conversation/message pagination, loaded-chat search, rename,
  confirmed deletion, generation, response retry, and cancellation;
- safe CommonMark/GFM response presentation with code blocks, tables, exact
  message copy, literal raw HTML, no rendered remote images, and no active
  model-authored links;
- owned attachment upload/download/deletion, vision images, and safe local
  TXT/CSV/PDF/DOCX document indexing;
- cited document retrieval in generated responses;
- explicit inspectable long-term memory with enable/disable and forget controls;
- a server-authorized Tools panel for calculator, local time, and owner-scoped
  document, Conversation, and memory search;
- a bounded Workflows panel for persisted one-to-eight-step owner research
  tasks, live progress, tool activity, safe results, and cancellation;
- a local Image studio for bounded generation, owned-source editing,
  cancellation, authenticated previews, and original/output comparison;
- bounded microphone recording or audio-file upload into local transcription,
  plus cancellable local read-aloud synthesis, playback, and deletion;
- a Settings & Diagnostics panel with an authoritative local capability matrix,
  fixed prerequisite guidance, owner session rotation/logout, system/dark/light
  appearance, and a direct path to memory controls;
- safe status-based errors, bounded media states, and history reconciliation
  after generation success or ambiguous failure.

The backend remains authoritative for authentication, ownership, persistence,
model resolution, generation admission, deadlines, and cancellation.

The client does not provision users or add a cloud provider. Image and voice
controls appear only when the backend reports a matching installed,
hardware-runnable local model and implemented adapter. Workflow execution is
limited to the fixed server tool registry; there is no arbitrary agent
autonomy.

## Validation

```bash
npm run typecheck
npm run lint
npm test
npm run build
```
