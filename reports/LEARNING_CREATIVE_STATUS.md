# Phase G Learning, Creative and Entertainment Activation Status

Evidence was refreshed on 2026-09-03 against the current RTX 3060 12 GB
workstation. Status labels distinguish installed runtime execution from
capability contracts that still require a verified model or provider.

| Capability | Status | Evidence or exact boundary |
|---|---|---|
| AI Teacher curriculum and lesson | RUNTIME PASS | A verified local model generated an untouched lesson with model identity and matching SHA-256 evidence |
| Exercises, quizzes and conversation practice | LOCAL PASS | Normalized answer verification, bounded attempts and no premature answer-key disclosure pass |
| Progress, adaptive difficulty and spaced repetition | RUNTIME PASS | Attempts persisted, difficulty adapted and deterministic review scheduling executed against disposable PostgreSQL |
| Multilingual text teaching | LOCAL/RUNTIME PASS | Instruction and target language tags, mixed Unicode answers and local-only private-memory routing pass |
| Acoustic pronunciation scoring | EXTERNAL/RUNTIME BLOCKED | Requires an admitted pronunciation model/provider, microphone permission, language coverage and end-to-end score validation |
| Image generation | RUNTIME PASS | Integrity-verified FLUX.2 Klein Base 4B FP8 generated and stored a real image on RTX 3060 |
| Image editing and inpainting | RUNTIME PASS | Real img2img and exact masked edit paths passed ownership, provenance and unchanged-region checks |
| Speech recognition | RUNTIME PASS | Faster Whisper CUDA transcribed a real audio asset with bounded GPU use |
| Speech synthesis | RUNTIME PASS | Piper produced and validated a real 22,050 Hz mono WAV asset |
| Interactive stories, text games and fictional characters | RUNTIME PASS | Local-only Agent OS generation, verifier digest, persistent history, completion and owner isolation passed |
| AI companion state/presence | LOCAL PASS | Shared presence-state resolution and web/mobile product paths pass |
| Safety isolation | LOCAL PASS | General-audience pre/post-generation gates, owner isolation and prohibited sexual/minor/non-consensual cases pass |
| Video, animation and generative audio creation/editing | RUNTIME BLOCKED | No separately integrity-verified runtime/model is installed and admitted |
| Protected adult experiences | OWNER ACTION/RUNTIME BLOCKED | No age/jurisdiction/consent control plane and verified protected runtime is configured |
| Extended female/multilingual speech profiles | RUNTIME BLOCKED | Current voice runtime is verified for synthesis; additional profile claims remain blocked until model-specific evidence exists |

## Verification

- Complete runtime regression: FLUX.2 generation, img2img and inpainting;
  Piper TTS; Faster Whisper STT; local teacher; progress/adaptation; spaced
  repetition; and local creative story all passed.
- Image peak evidence remained inside the existing guarded route (11,040 MiB
  observed during the full runtime gate); the worker stopped cleanly afterward.
- Focused backend: 82 passed and 4 intentional PostgreSQL-environment skips.
- Focused web: 13 passed.
- Focused mobile: 5 passed.
- Disposable PostgreSQL regression: 48 passed through migration `0018` with no
  drift.

No unavailable media capability is promoted to ready. Model admission,
integrity, hardware guards, owner permissions, asset provenance and verified
runtime output remain mandatory before a blocked capability can become live.
