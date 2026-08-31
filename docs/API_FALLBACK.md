# Local-first External AI fallback

External AI is an optional subsystem under Settings → API / External AI. It is
disabled when `EXTERNAL_AI_STATE_ROOT` is absent, disabled globally by default,
and each newly saved provider is disabled by default.

## Providers and key isolation

Implemented adapters use fixed official HTTPS origins for OpenAI, Anthropic and
Google. Custom origins, redirects and proxy environment variables are refused,
which prevents provider configuration from becoming an SSRF mechanism. The
service supports multiple independently enabled provider records.

Keys are accepted only in authenticated write bodies. Responses contain
`key_configured`, never the key. `EncryptedProviderVault` stores the complete
provider state using libsodium XChaCha20-Poly1305 in an owner-only directory,
with a separate mode-0600 256-bit key, authenticated ciphertext, atomic writes
and symlink/tamper rejection. Keys are used only in provider authorization
headers and are never included in model messages, logs, URLs or diagnostics.

## Production admission

Discovering a provider model never admits it. A production route requires a
content-addressed record under `verification-evidence/`. The record must state a
passing complete-category benchmark, provider kind, exact model ID/tasks,
positive measured quality/stability/context, latency and costs. Its SHA-256
filename must match its content. For verified models the server reconstructs
the routing policy from that immutable record; it does not trust client-supplied
measurement fields.

Register a locally produced benchmark record with:

```bash
backend/.venv/bin/python scripts/external_model_evidence.py \
  --state-root /absolute/owner-only/external-ai \
  /absolute/path/to/complete-category-evidence.json
```

The command prints the evidence SHA-256 used in Settings. An arbitrary digest,
unknown model, partial benchmark or mismatched metric fails closed.

## Selection and accounting

The routing order is:

```text
eligible local route(s)
→ configured free-tier external choices
→ cheapest eligible external choice
→ higher-quality/stability/lower-latency tie-breaks
```

Every choice must satisfy task and context. The service enforces provider rate,
remaining token quota, timeout and projected spend ceiling before a request.
Projected quota and cost are reserved before network use, so parallel requests
cannot race past a configured limit; the reservation is released on failure or
settled to actual provider usage on success. Actual input/output token usage is
recorded atomically in the encrypted vault.
Authenticated settings and diagnostics expose status, quota and micro-unit cost,
not keys or provider request bodies.

Provider API calls are an external boundary: a key, account, quota and network
must legitimately exist. WORK STATION never scrapes, shares or fabricates keys.
