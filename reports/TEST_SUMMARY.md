# Final Test Summary

## Latest complete local release gate

| Suite | Result |
| --- | --- |
| Feature registry | 245 validated; unique IDs and complete UI/backend/dependency/coverage records |
| Backend | 2,926 passed, 48 skipped, 1 third-party deprecation warning |
| Web | 191 passed across 35 files |
| Mobile | 61 passed across 18 files |
| Expo Doctor | 21/21 |
| Desktop Rust | 2 passed |
| PostgreSQL | 48 passed; migrations `0001` through `0017`; no drift |
| Web build | PASS; 485,078-byte initial entry; 11 lazy workspaces |
| Linux packages | AppImage and DEB built; production/AppImage launch PASS |
| Native Android | Optimized APK build (532 Gradle tasks), lint, identity, signing, alignment, artifact safety PASS |
| Browser/PWA E2E | Install, authenticated registry, chat, cache isolation, logout PASS |
| Service/gateway | systemd units and private-gateway/Funnel guards PASS |

One Expo Doctor request received a transient directory-service response during
a later regression rerun. The existing bounded retry surfaced it, retried once,
and then passed 21/21. It was not recorded as a product pass before recovery.

## Real runtime matrix

The final runtime execution passed:

- vision against the admitted local vision model (8,538 MiB observed GPU
  memory and 96% utilization during the probe);
- RAG with 768-dimensional local embeddings, citations, owner isolation and
  tombstoning;
- long-term memory with provenance, owner isolation, disable, forget and
  redaction controls;
- FLUX.2 Klein generation, img2img and exact masked inpainting with ownership,
  provenance, cleanup and bounded GPU guards;
- local STT/TTS voice flow and truthful state transitions;
- Agent OS plan/execute/verify/SSE flow in one attempt, measured at 9,270 ms;
- encrypted owner-scoped connector health/action/retry/audit/revocation;
- marketing research/strategy/content/creative, owner-approved publishing and
  source-grounded analytics;
- finance research, backtesting, paper trading, portfolio/risk/alerts/journal;
- local teacher generation, adaptive progress and spaced repetition;
- creative story generation, integrity and audience controls;
- bounded tools with explicit denial of shell/code/unrestricted network; and
- workflow terminal states, cancellation/failure/restart and audit linkage.

## Preserved scale and quality evidence

The million-interaction test was not rerun because no core scale path required
it. Its stored machine report was rehashed and remains:

- 1,000,000 completed; 1,000,000 passed; 0 failed;
- disposable database, production data unchanged;
- 1,806.412 seconds; average 0.073693 s; p95 0.134651 s;
- report SHA-256 `d535abaf1dc8ce49536185e4b46996bcb65337b9839fb9be2e189bbf303c238a`.

The latest canonical AI benchmark was also preserved because Step 1–10 made no
benchmark, prompt, scoring or production model-route change:

- 459 cases; 97.88/100; 457 PASS, 1 PARTIAL, 1 FAIL;
- Safety 100%; hallucination 0%; executable code 24/24;
- average latency 7.6724 s; p95 15.6681 s; duration 3,554.62 s;
- summary SHA-256 `436e6fe8a4da50dda368de3c94ce3f0ad1d47e863c7bdee46bd3b47fe4dbfe0e`;
- results SHA-256 `0f66871f34ab18ea37d56381fe93a70b77a223d95607644aba5ab9f6fdb47913`.

The remaining `medium-coding-04` PARTIAL and
`model-comparison-coder-06` FAIL are unchanged local coder-model limitations.
No output rewriting, expected-answer change, checker weakening, external API,
or benchmark-specific production rule was used.
