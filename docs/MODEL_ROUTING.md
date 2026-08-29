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

Image selection is capability-based through `ImageModelContract`. SDXL Base 1.0
remains the current verified fallback; Lightning, FLUX-family, and future models
require their own verified workflow adapter and admission metadata. Vision input
and image generation/editing remain separate capabilities.

Ollama, ComfyUI, speech runtimes, and future inference servers are adapters below
the catalog. Frontend, API, database, RAG, memory, conversation, tool, and workflow
code does not branch on runtime or hardware.
