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

Ollama discovery uses `GET /api/tags` only for installed-model inventory. The
exact local-model allowlist is applied to that inventory before any detail
request. For each installed and allowlisted reference, the adapter calls
`POST /api/show` with only that internal reference and reads only the documented
`capabilities` list. Ollama `completion`, `vision`, `embedding`, and `tools`
values map respectively to the public `text_generation`, `vision_input`,
`embeddings`, and `tool_calling` capabilities. Unknown values are ignored.
Templates, parameters, model information, licenses, paths, filenames, tags, and
other detail fields are never used to infer capabilities or promoted to the
public response. Missing, malformed, or unavailable capability details fail
closed as a generic unavailable local model runtime.

Parameter classes such as 7B, 14B, 32B, and 70B+ are descriptive metadata, not
application routing branches. Future generation requests can select the public
`model_id`; the catalog can then resolve the appropriate local adapter without
changing User, Conversation, or Message persistence.

This endpoint performs inventory discovery only. It does not generate text,
stream output, load or download models, pull tags, start jobs, choose a global
active model, or persist a user preference.

## Non-streaming conversation generation

All generation requests first pass through the global raw HTTP body limit.
`REQUEST_MAX_BODY_BYTES` is a strict integer from 1 through 1,048,576 bytes
and defaults to 262,144 bytes (256 KiB). A valid declared length above the cap
is rejected with HTTP 413 before body consumption, authentication, Conversation
lookup, generation admission, catalog discovery, runtime dispatch, or
persistence. Missing or understated declarations remain subject to cumulative
actual-byte counting. Invalid or ambiguous `Content-Length` receives HTTP 400.
The generic 413 message is `Request body is too large`; responses expose no
limit, count, header, body, credential, or field content. This is an ingress
byte limit only and does not add a Message-field, database, context,
Ollama-response, WebSocket, rate, quota, or deadline limit.

An authenticated user may generate one local assistant response for an owned
Conversation:

    POST /api/v1/conversations/{conversation_id}/messages/generate
    Authorization: Bearer <access_token>
    Content-Type: application/json

    {"model_id":"ollama-local:<opaque-id>","user_message":"Optional follow-up","max_output_tokens":128,"temperature":0.5,"seed":42,"top_p":0.9,"top_k":40,"min_p":0.05,"repeat_penalty":1.1,"repeat_last_n":64,"typical_p":0.7,"presence_penalty":1.5,"frequency_penalty":0.75,"stop_sequences":["\n","END","\n"]}

The public model_id must come from the model catalog. An optional nonblank
`user_message` is committed first as an owner-scoped, server-assigned user
Message; omission or null retains generation-only behavior. An optional strict
integer `max_output_tokens` from 1 through 1,024 may lower the response bound;
omission retains the 1,024-token default. An optional finite numeric
`temperature` from 0.0 through 2.0 may control sampling; integers in that
range are accepted, while explicit null, booleans, strings, non-finite values,
and out-of-range values are rejected. Omitting `temperature` leaves Ollama's
current temperature behavior unchanged. Raw runtime tags, runtime URLs, owner
IDs, roles, sequences, Message arrays, raw `stop`, arbitrary generation
options, other sampling controls, and streaming flags are not accepted.

The resolved catalog model must advertise `text_generation`. Models that expose
only capabilities such as `embeddings` or `vision_input` remain catalog-visible
but are rejected from Conversation text generation with HTTP 409 before the
runtime router is invoked or an assistant Message is persisted. When the same
request committed an optional `user_message`, that committed Message remains
available for a generation-only retry with a text-generation-capable model.

An optional strict integer `seed` from 0 through 2,147,483,647 is accepted.
Explicit null, booleans, strings, floats, arrays, objects, non-finite values,
negative values, and values above that bound are rejected. Omitting `seed`
adds no seed option to the Ollama request. A seed can support repeatability only
with the same model, context, runtime, and runtime version; it is not a
cross-version determinism guarantee.

An optional finite numeric `top_p` from 0.0 through 1.0 is accepted, including
integer values 0 and 1. Explicit null, booleans, strings, arrays, objects,
non-finite numbers, negative values, and values above 1.0 are rejected.
Omitting `top_p` adds no top-p option to the Ollama request. Supplied values are
forwarded only as the bounded Ollama `options.top_p` sampling control.

An optional strict integer `top_k` from 1 through 100 is accepted. Explicit
null, booleans, strings, floats, arrays, objects, non-finite values, zero,
negative values, and values above 100 are rejected. Omitting `top_k` adds no
top-k option to the Ollama request. Supplied values are forwarded only as the
bounded Ollama `options.top_k` sampling control.

An optional finite numeric `min_p` from 0.0 through 1.0 is accepted, including
integer values 0 and 1. Explicit null, booleans, strings, arrays, objects,
non-finite numbers, negative values, and values above 1.0 are rejected.
Omitting `min_p` adds no min-p option to the Ollama request. Supplied values are
forwarded only as the bounded Ollama `options.min_p` sampling control.

An optional finite numeric `repeat_penalty` from 0.5 through 2.0 is accepted,
including integer values 1 and 2. Explicit null, booleans, strings, arrays,
objects, non-finite numbers, values below 0.5, and values above 2.0 are
rejected. Omitting `repeat_penalty` adds no repetition-penalty option to the
Ollama request. Supplied values are forwarded only as the bounded Ollama
`options.repeat_penalty` control.

An optional strict integer `repeat_last_n` from 0 through 2,048 is accepted.
Zero explicitly disables repetition lookback, while positive values select a
bounded token-history window. Explicit null, booleans, strings, floats, arrays,
objects, non-finite values, negative values including Ollama's `-1` sentinel,
and values above 2,048 are rejected. Omitting `repeat_last_n` adds no
repetition-window option to the Ollama request. Supplied values are forwarded
only as the bounded Ollama `options.repeat_last_n` control.

An optional finite numeric `typical_p` from 0.0 through 1.0 is accepted,
including integer values 0 and 1. Explicit null, booleans, strings, arrays,
objects, non-finite numbers, negative values, and values above 1.0 are rejected.
Omitting `typical_p` adds no locally typical sampling option to the Ollama
request. Supplied values are forwarded only as the bounded Ollama
`options.typical_p` sampling control.

An optional finite numeric `presence_penalty` from -2.0 through 2.0 is
accepted, including integer values within that range. Positive values
discourage reuse of tokens already present, negative values encourage reuse,
and zero applies no presence penalty. Explicit null, booleans, strings, arrays,
objects, non-finite numbers, values below -2.0, and values above 2.0 are
rejected. Omitting `presence_penalty` adds no presence-penalty option to the
Ollama request. Supplied values are forwarded only as the bounded Ollama
`options.presence_penalty` control.

An optional finite numeric `frequency_penalty` from -2.0 through 2.0 is
accepted, including integer values within that range. Positive values
discourage tokens proportionally to their prior frequency, negative values
encourage reuse, and zero applies no frequency penalty. Explicit null,
booleans, strings, arrays, objects, non-finite numbers, values below -2.0, and
values above 2.0 are rejected. Omitting `frequency_penalty` adds no
frequency-penalty option to the Ollama request. Supplied values are forwarded
only as the bounded Ollama `options.frequency_penalty` control.

An optional `stop_sequences` JSON array containing one through four strict
strings is accepted. Each string must contain one through 128 Unicode
characters. Exact content and order are preserved without trimming,
normalization, or duplicate removal. This includes non-empty whitespace and
control text representable as JSON strings, such as `"\n"`. Explicit null,
scalar values, objects, an empty array, more than four entries, non-string
entries, empty strings, and strings longer than 128 characters are rejected.
Omission is the only way to preserve model or runtime stop defaults and adds no
`stop` key to the Ollama request. Supplied sequences are forwarded unchanged
only as Ollama `options.stop`.

Conversation creation may persist one optional client-authored `system_prompt`
as a server-assigned system Message before the required initial user Message.
Both Messages are owner-scoped and committed atomically with the Conversation.
The exact system content is then consumed from persisted Conversation history;
the generation endpoint cannot supply or change a system prompt and still
accepts no client-controlled role fields.

The required `initial_message` at Conversation creation and required `content`
at the standalone user-Message append endpoint must each contain at least one
non-whitespace character. Their exact leading and trailing whitespace is
preserved, and neither field gains a semantic field-length limit. The complete
HTTP request remains subject to `REQUEST_MAX_BODY_BYTES`. This validation does
not change the generation endpoint's optional nonblank `user_message`:
omission or null still performs generation only, while supplied content is
still committed before generation.

The application accepts at most 100 existing Messages in ascending sequence
order, with a fixed 100,000-character context bound. It fetches up to 101
Messages so the extra row can detect overflow. The history must be contiguous,
must end in a user Message, and may contain only system, user, and assistant
roles. Prompt construction uses a dedicated owner-scoped internal context
query; it does not reuse public Message pagination, cursors, or offsets, and
oversized histories are rejected rather than truncated. The output bound
defaults to 1,024 tokens and may be lowered per request without exceeding that
ceiling. A supplied temperature is forwarded through the runtime-neutral
generation boundary; omission does not add a temperature option to the Ollama
request. A supplied seed is forwarded unchanged through the same boundary;
omission does not add a seed option to the Ollama request. A supplied `top_p`
is forwarded unchanged through the same boundary; omission does not add a
top-p option to the Ollama request. A supplied `top_k` is forwarded unchanged
through the same boundary; omission does not add a top-k option to the Ollama
request. A supplied `min_p` is forwarded unchanged through the same boundary;
omission does not add a min-p option to the Ollama request. A supplied
`repeat_penalty` is forwarded unchanged through the same boundary; omission
does not add a repetition-penalty option to the Ollama request. A supplied
`repeat_last_n` is forwarded unchanged through the same boundary; omission does
not add a repetition-window option to the Ollama request. A supplied
`typical_p` is forwarded unchanged through the same boundary; omission does not
add a locally typical sampling option to the Ollama request. A supplied
`presence_penalty` is forwarded unchanged through the same boundary; omission
does not add a presence-penalty option to the Ollama request. A supplied
`frequency_penalty` is forwarded unchanged through the same boundary; omission
does not add a frequency-penalty option to the Ollama request. Supplied
`stop_sequences` are forwarded unchanged through the same boundary and only as
Ollama `options.stop`; omission adds no stop option. Model parameter class is
not used for routing or policy.

The successful HTTP 201 response contains the selected public model_id and the
newly persisted assistant Message. Ollama is invoked through the runtime-neutral
text-generation boundary using /api/chat with stream set to false. Generation
may cause Ollama to load the selected model into memory. The application does
not override `keep_alive`, so Ollama's configured keep-alive policy applies;
Ollama's default is to retain a loaded model for five minutes. The application
does not pull or download models and exposes no preload, unload, or global
model-selection endpoint.

Generation admission is fail-fast and process-local. Configure
`GENERATION_MAX_ACTIVE_PER_PROCESS` as a strict integer from 1 through 8; the
default is 1. The bound is intentionally conservative for a single-process
local AI service and values outside that range are rejected at startup. At
most one generation may be active for an authenticated user UUID, even across
different Conversations, and different users may proceed only until the
process-wide cap is reached. Token rotation does not create a new admission
identity because admission uses the stable authenticated user UUID.

Conversation ownership is confirmed before admission. An admitted permit is
acquired before an optional user Message is appended, before context or model
discovery, and before runtime dispatch. A request rejected by either the
per-user or process-wide bound receives HTTP 429 with `Generation capacity is
busy`; it does not persist its optional `user_message` or invoke the local
runtime, so the client may retry the same request later. Admission does not
wait or create a queue. The permit is released after assistant persistence or
on every failure, stale-generation rejection, and request cancellation. This
controller coordinates one application process only; it adds no Redis lease,
database lock, distributed scheduler, or job system.

The service copies the owner-scoped Conversation history and rolls back its
read transaction before local inference. The generated assistant Message is
then appended through the existing atomic Message sequence allocator. That
append includes the Conversation's captured next sequence as a compare
condition. If another Message arrives during inference, the stale assistant
output is rejected with HTTP 409 and is never persisted.

When `user_message` is supplied, its commit completes before context capture
and local inference. The captured context must end at exactly that new user
Message; a Message that wins the race before capture causes HTTP 409 without
invoking the model. Later model, context, runtime, or assistant-persistence
failures leave the committed user Message intact and persist no assistant, so a
client may retry with a generation-only request.

Error behavior is intentionally safe:

- missing and foreign-owned Conversations return the same generic HTTP 404;
- an unknown public model ID returns HTTP 404;
- unsupported Conversation state, a model without the `text_generation`
  capability, an unsupported generation adapter, or a changed Conversation
  returns HTTP 409;
- oversized context returns HTTP 413;
- a model marked unavailable and unavailable or malformed local runtime
  responses return the same generic HTTP 503;
- unexpected failures retain the application's generic HTTP 500 response.

Runtime references, runtime URLs, local paths, credentials, hardware
identifiers, persistence details, and internal exception text are not returned.
This slice does not add streaming, client-controlled generation options beyond
the bounded output-token, temperature, seed, top-p, top-k, min-p,
repeat-penalty, repetition-window, locally typical sampling, presence-penalty,
frequency-penalty, and bounded stop-sequence fields, tools, model preferences,
explicit model lifecycle controls, image/audio/video generation, or any cloud
AI dependency.
