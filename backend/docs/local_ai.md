# Local AI model discovery

AI Workspace discovers models only from explicitly configured local runtimes.
No cloud AI provider is required or supported by this boundary. Ollama is the
first adapter, while the application-facing catalog remains runtime-neutral.

Set `OLLAMA_BASE_URL` to a loopback-only origin such as
`http://127.0.0.1:11434`. Runtime URLs containing credentials, paths, query
parameters, fragments, non-loopback IP addresses, or nonlocal hostnames are
rejected. Runtime HTTP calls do not inherit environment proxies and do not
follow redirects.

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
