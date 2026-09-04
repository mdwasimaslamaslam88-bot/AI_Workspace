# Final Security Report

## Gate result

**PASS with recorded moderate transitive build-tool risk.**

- Critical findings: **0**
- High findings: **0**
- Moderate findings: **14**

The moderate findings are transitive Expo build-tool advisories. Forced
upgrades would replace the compatible Expo stack without regression evidence,
so the residual is documented rather than hidden or automatically forced.
Python package consistency passed.

## Verified controls

- owner authentication/isolation across conversations, memory, RAG, missions,
  sessions, connectors, finance, learning, creative assets and workflows;
- encrypted write-only provider credentials excluded from prompts, API output,
  logs, reports and audit payloads;
- exact connector origin/path/scope enforcement, redirect/SSRF denial, bounded
  payloads, timeouts, retry/rate/circuit-breaker controls and revocation;
- production external egress deny-all with zero approved external origins;
- persistent mission approval invalidation, bounded retry, cancellation,
  recovery and durable owner-scoped audit events;
- paper/live broker separation, risk policy, kill switch, idempotency,
  provider acknowledgement and independent status/fill verification;
- document injection/secret redaction, traversal-safe storage and controlled
  tool permissions;
- loopback backend, authenticated private TLS remote access and public Funnel
  disabled;
- PostgreSQL migrations through `0022_persistent_agent_missions` without drift;
- self-update isolation, last-known-good backup, release gates and rollback;
- Windows/macOS/Android/Linux/Web artifact identity, archive, path and secret
  checks.

## Consolidated artifact classification

All eight artifacts passed checksums and extracted-content scans. No private
workspace path or credential-shaped token was found. Broad PEM marker scanning
identified only:

- AppImage `usr/lib/libgnutls.so.30`, SHA-256
  `76821a78bc2a273aae74b90750036a392de18b93180504343e607b4c69a82692`,
  containing upstream `test_dh`/`test_known_sig` self-test fixtures; and
- AppImage `usr/lib/libgio-2.0.so.0` and its versioned target, both SHA-256
  `5f02b1437b08cafd094d7236b134453a6a232419efedcd1def131a2ff80ccafb`,
  containing PEM parser labels/diagnostics rather than key material.

Any marker outside those exact fixed-hash dependency files was a hard failure.
This corrects the older imprecise statement that packaged files matched the
currently installed system-library hashes; the classification is based on
fixed packaged hashes and verified upstream self-test/parser context.

## Trust boundaries

Windows is unsigned, macOS is ad-hoc signed, Android uses the standard debug
certificate and iOS has no native package claim. Owner identities, signing,
notarization, store approval and physical-device validation remain external.
No claim of being unhackable is made.
