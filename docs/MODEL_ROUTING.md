# Model routing

Task routing operates only on admitted `ModelDescriptor` values. It does not know
the GPU model. The supported tasks are general chat, reasoning, mathematics,
debugging, coding, code generation, exact output, expert analysis, RAG, memory,
vision, long context, summarization, tools, workflows, embeddings, voice, image
generation, and image editing.

The router filters by installation, availability, admission, required capability,
and context. It then considers an explicit evidence-backed preference, whole-task
quality/stability/latency evidence when supplied, scale/family capability signals,
VRAM cost, and deterministic model ID ordering. A preferred model that becomes
ineligible is skipped and its admitted fallback is used.

Current production preferences remain structured configuration in
`OLLAMA_TASK_MODEL_PREFERENCES`; this architecture change does not alter them.
Installing a larger model does not automatically displace a proven smaller model.
The candidate must be verified, admitted, benchmarked on the complete relevant
category, stable, and then selected by policy.

Image selection is capability-based through `ImageModelContract`. The current
route is FLUX.2 Klein Base 4B FP8 through its fixed, integrity-checked ComfyUI
generation/reference-edit/inpainting adapter. SDXL Base 1.0 remains the verified
fallback. Lightning and future image families require their own verified
workflow adapter, artifact manifest, complete-category evidence, and admission
metadata. Vision input and image generation/editing remain separate capabilities.

Ollama, ComfyUI, speech runtimes, and future inference servers are adapters below
the catalog. Frontend, API, database, RAG, memory, conversation, tool, and workflow
code does not branch on runtime or hardware.

Agent OS uses `LocalFirstModelSelector`: it exhausts admitted local choices and
only then consults enabled External AI records. External models must match a
registered content-addressed complete-category evidence record; a discovered
name or user-supplied `verified` boolean is insufficient. Free-tier choices sort
before paid choices, then combined token cost, measured quality, stability,
latency, provider priority and deterministic identity. Spend, quota, context and
rate limits can remove a route at call time.

## Current hardware discovery evidence (2026-08-31)

Complete-category comparisons on the RTX 3060 produced no justified production
route change. The existing winners remain Qwen 2.5 Coder 7B for coding, Qwen 3
8B for debugging/reasoning/mathematics/exact output, and Gemma 4 12B for the
executable-code category. Qwen 2.5 VL 7B remains the vision route at 99.5; Gemma
4 12B tied its score but was slower, so the tie did not displace the fallback.

DeepCoder 14B was stopped after 209 of 221 cases by the GPU thermal safety guard
and has no admission score. Its earlier complete report was archived and the
current report is an explicit failed-run record, preventing stale success from
being aggregated. Qwen 3 14B and Phi-4 14B evidence used a different capability
matrix fingerprint from the production baselines and was conservatively
excluded rather than compared across incompatible matrices. These exclusions
are preserved in `current-hardware-model-discovery.json`; the resulting routing
recommendation contains no changes.

## Phase 1 image admission evidence (2026-09-01)

The unchanged 459-case benchmark admitted FLUX.2 Klein Base 4B FP8 after all 13
image cases passed and the image score rose from 93.88 to 99.42. The previously
partial single-object and masked-inpainting cases became PASS. The production
runtime smoke also passed generation, img2img, inpainting, owner isolation,
provenance, cleanup, and clean shutdown with 10,940 MiB peak process GPU memory.
The model is admitted as `runnable_with_offload`, with one-operation concurrency,
the unchanged 1.5 GiB ComfyUI reserve, and measured `acceptable` performance.

Text candidates did not qualify. Qwen 2.5 Coder 7B remains the coding route;
Qwen 3 8B remains the reasoning, mathematics, debugging, expert-analysis, and
exact-output route; Gemma 4 12B remains the executable-code route. The exact
candidate matrix and remaining limitations are in `AI_QUALITY_PHASE1.md`.
