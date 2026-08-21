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

## Current workflow

The MVP supports:

- current-user validation;
- selection of available models that advertise `text_generation`;
- real backend conversation keyset pagination;
- conversation creation with supported optional title and system prompt;
- bounded message pagination;
- non-streaming generation and explicit cancellation;
- safe status-based errors with an optional backend `X-Request-ID`;
- history reconciliation after generation success or ambiguous failure.

The backend remains authoritative for authentication, ownership, persistence,
model resolution, generation admission, deadlines, and cancellation.

This milestone does not add streaming, files, images, memory, voice, tools,
agents, cloud providers, or a new authentication mechanism.

## Validation

```bash
npm run typecheck
npm run lint
npm test
npm run build
```
