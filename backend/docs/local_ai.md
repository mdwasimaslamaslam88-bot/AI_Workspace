# Local AI model discovery

AI Workspace discovers models only from explicitly configured local runtimes.
No cloud AI provider is required or supported by this boundary. Ollama is the
first adapter, while the application-facing catalog remains runtime-neutral.

Set `OLLAMA_BASE_URL` to a loopback-only origin such as
`http://127.0.0.1:11434`. Runtime URLs containing credentials, paths, query
parameters, fragments, non-loopback IP addresses, or nonlocal hostnames are
rejected. Runtime HTTP calls do not inherit environment proxies and do not
follow redirects.

`OLLAMA_LOCAL_MODEL_ALLOWLIST` is a JSON array of exact Ollama model
references that the deployment owner has verified execute from local model
data. It defaults to an empty array, so no Ollama model is discoverable or
eligible for generation until explicitly approved. Cloud-backed references
and aliases must never be added. The allowlist is enforced while parsing the
inventory and again immediately before generation because a loopback Ollama
endpoint alone does not prove that inference stays on the local machine.

Authenticated clients may call:

```http
GET /api/v1/ai/models
Authorization: Bearer <access_token>
```

An unconfigured runtime returns HTTP 200 with an empty inventory:

```json
{"items": []}
```

Each discovered model is represented by safe, normalized metadata:

- `model_id`: stable runtime-namespaced public identifier;
- `display_name`;
- `runtime_id`;
- `modality`;
- nullable `family` and `parameter_class`;
- normalized `capabilities`;
- nullable `context_window`, `quantization`, and `estimated_vram_bytes`;
- `availability`.

The public identifier is derived from—but does not expose—the runtime's opaque
model reference. Runtime URLs, raw tags/references, local filesystem paths,
hardware identifiers, and credentials are never included in the response.
Unknown metadata remains `null` instead of being inferred from a model name.

Parameter classes such as 7B, 14B, 32B, and 70B+ are descriptive metadata, not
application routing branches. Future generation requests can select the public
`model_id`; the catalog can then resolve the appropriate local adapter without
changing User, Conversation, or Message persistence.

This endpoint performs inventory discovery only. It does not generate text,
stream output, load or download models, pull tags, start jobs, choose a global
active model, or persist a user preference.

## Non-streaming conversation generation

An authenticated user may generate one local assistant response for an owned
Conversation:

    POST /api/v1/conversations/{conversation_id}/messages/generate
    Authorization: Bearer <access_token>
    Content-Type: application/json

    {"model_id":"ollama-local:<opaque-id>"}

The public model_id must come from the model catalog. Raw runtime tags, runtime
URLs, prompts supplied outside Conversation history, owner IDs, generation
options, and streaming flags are not accepted.

Conversation creation may persist one optional client-authored `system_prompt`
as a server-assigned system Message before the required initial user Message.
Both Messages are owner-scoped and committed atomically with the Conversation.
The exact system content is then consumed from persisted Conversation history;
the generation endpoint itself still accepts no prompt or role fields.

The application accepts at most 100 existing Messages in ascending sequence
order, with a fixed 100,000-character context bound. It fetches up to 101
Messages so the extra row can detect overflow. The history must be contiguous,
must end in a user Message, and may contain only system, user, and assistant
roles. Prompt construction uses a dedicated owner-scoped internal context
query; it does not reuse public Message pagination, cursors, or offsets, and
oversized histories are rejected rather than truncated. The initial output
bound is fixed at 1,024 tokens. Model parameter class is not used for routing
or policy.

The successful HTTP 201 response contains the selected public model_id and the
newly persisted assistant Message. Ollama is invoked through the runtime-neutral
text-generation boundary using /api/chat with stream set to false. Generation
may cause Ollama to load the selected model into memory. The application does
not override `keep_alive`, so Ollama's configured keep-alive policy applies;
Ollama's default is to retain a loaded model for five minutes. The application
does not pull or download models and exposes no preload, unload, or global
model-selection endpoint.

The service copies the owner-scoped Conversation history and rolls back its
read transaction before local inference. The generated assistant Message is
then appended through the existing atomic Message sequence allocator. That
append includes the Conversation's captured next sequence as a compare
condition. If another Message arrives during inference, the stale assistant
output is rejected with HTTP 409 and is never persisted.

Error behavior is intentionally safe:

- missing and foreign-owned Conversations return the same generic HTTP 404;
- an unknown public model ID returns HTTP 404;
- unsupported Conversation state, unsupported generation adapter, or a changed
  Conversation returns HTTP 409;
- oversized context returns HTTP 413;
- a model marked unavailable and unavailable or malformed local runtime
  responses return the same generic HTTP 503;
- unexpected failures retain the application's generic HTTP 500 response.

Runtime references, runtime URLs, local paths, credentials, hardware
identifiers, persistence details, and internal exception text are not returned.
This slice does not add streaming, client-controlled generation options, tools,
model preferences, explicit model lifecycle controls, image/audio/video
generation, or any cloud AI dependency.
