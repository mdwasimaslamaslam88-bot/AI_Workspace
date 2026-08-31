# AI quality Phase 1 evidence

This record describes the measured 2026-09-01 quality push. It is evidence,
not a claim of universal correctness. The canonical prompts, expected answers,
objective checks, scoring rules, generated outputs, and local-only policy were
not changed. External API fallback was not used.

## Reproduced baseline

The fresh baseline report contained 459 cases, scored 97.74/100, and recorded
455 PASS, 3 PARTIAL, and 1 FAIL. Safety was 100%, hallucination was 0%, and all
24 executable-code artifacts passed. Its summary and result SHA-256 values were
`afbfba66634711d7b4e16448935ba0fabdc048d43c9f089e1324c1514f9ca032`
and `c045e27cb8530c03dc9791278ca21087c5c5bb9778d3516d53b90939454a398b`.

The four non-passes were reproduced before production changes:

| Test | Model/profile | Untouched raw output | Expected/checker | Classification | Latency and resources |
| --- | --- | --- | --- | --- | --- |
| `model-comparison-coder-06` | Qwen 2.5 Coder 7B, canonical coder profile | `Infinite Recursion` | Required case-insensitive term `base case` and name-only form | `MODEL LIMITATION` and `STOCHASTIC`; the fixed coder comparison role is not a production routing failure | Canonical 2.7232 s; isolated deterministic probe 9.9693 s, 4,995 MiB peak GPU memory, 78.16 GB minimum available RAM |
| `medium-coding-04` | Qwen 2.5 Coder 7B, canonical coder profile | fenced `[0**2, 1**2, 2**2, 3**2, 4**2]` | Literal `x * x` and `range(5)` checks; independent probe also parsed AST, compiled, executed in a restricted environment, and required the exact list-comprehension AST | `MODEL LIMITATION` and `BENCHMARK CONTRACT ISSUE`; the answer computes the correct list, while the prompt does not request the checker-specific spelling | Canonical 3.4126 s; isolated probe 3.1700 s, 4,995 MiB peak GPU memory, 78.18 GB minimum available RAM |
| `image-generation-01` | SDXL Base 1.0, fixed seed | PNG SHA-256 `c2cdbe2aa05c517a522c0e09a6ca3116ad65c49ead1769b1c99e22179f724b14`; judge observed four red circles | One centered solid red circle on white, with no extra objects or text; PNG/provenance checks plus independent vision evaluation | `IMAGE MODEL LIMITATION` | 29.7067 s; the verified SDXL runtime smoke used 8,392 MiB peak process GPU memory |
| `image-editing-03-inpainting` | SDXL Base 1.0, fixed seed and mask | PNG SHA-256 `4361f5c63143bfa9c34aa3cf82e0c6a844ac676a3dad5d41c5f7e7a1fff4f143`; judge observed a yellow/green incomplete cube and extra red elements | Bright yellow square only in the masked center, source provenance preserved; image judge and source-integrity checks | `IMAGE MODEL LIMITATION` | 27.5422 s; same bounded SDXL runtime profile |

## Text candidate result

Nine already-installed candidates were tested at temperature 0 and fixed seed:
Qwen 3 8B, Qwen 2.5 Coder 7B, Qwen 2.5 Coder 14B Q3, Qwen 3 14B Q4,
DeepCoder 14B Q4, Gemma 4 12B Q4, Qwen 3.5 9B Q4, Ministral 3 14B Q4,
and Phi-4 14B Q4. Qwen 3 exact-output thinking on/off/automatic, coder
`top_k=1`, and three coder runs at temperature 0.2 were also tested.

Qwen 3 8B, Qwen 3 14B, and Gemma 4 12B produced `base case`, but the
model-comparison case intentionally measures the coder model. The coder produced
`base case` in only one of three temperature-0.2 runs, so that profile was not
stable. Every candidate missed the exact `x * x` AST contract; several produced
semantically correct `x**2` comprehensions. No text route/profile met the rule
to fix its target without reducing the complete relevant category or creating
an equivalent regression. Production text routing therefore remained unchanged.

## Image candidate and admission

The selected candidate is the official Apache-2.0 FLUX.2 Klein Base 4B FP8
release at revision `103db268c10d4d3921101b46057671f9ac460da6`. Production
startup fails closed unless all three exact artifacts are present and match:

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `flux-2-klein-base-4b-fp8.safetensors` | 4,089,498,488 | `44bab3a86fe98b85d21dd2a4729ebdc3ae51fb8a39f76e457e18c724219e6840` |
| `qwen_3_4b.safetensors` | 8,044,982,048 | `6c671498573ac2f7a5501502ccce8d2b08ea6ca2f661c458e708f36b36edfc5a` |
| `flux2-vae.safetensors` | 336,213,556 | `d64f3a68e1cc4f9f4e29b6e0da38a0204fe9a49f2d4053f0ec1fa1ca02f9c4b5` |

An isolated fixed-seed probe used the canonical prompts and parameters. The raw
single-circle PNG, without post-processing, had SHA-256
`336faef9bfb56894b766a2571dd71b9dffac7376998434b80d410b7ce1432f0a`.
The pixel checker found exactly one red component, 0.9947 aspect ratio, 0.7846
fill, and a 0.5827 white-background fraction; all composition checks passed in
18.8323 seconds. The exact-mask edit produced raw SHA-256
`8ba9af8a3f5542683c6441bb5db67d03b9be61ecde5263a017bd0581c45b8e98`.
It produced one square-like yellow component, 0.9726 center-yellow fraction,
and 2.932 mean absolute pixel difference outside the mask; every masked-edit
check passed in 46.1541 seconds. A source-reference-only control failed the
outside-mask preservation check, proving that the mask-enforced graph is
required rather than merely cosmetic.

The isolated probe peaked at 11,400 MiB process GPU memory and 66 degrees
Celsius with at least 64.88 GB RAM available. The production runtime smoke
passed generation, img2img, inpainting, ownership, provenance, cleanup, and
clean shutdown at 10,940 MiB peak process GPU memory and 100% GPU utilization.
The authoritative admission status is `runnable_with_offload`: 11.5 GiB full
working set, 10.5 GiB low-VRAM minimum, 32 GiB RAM, CPU offload, acceptable
measured performance, one active operation, and the unchanged 1.5 GiB ComfyUI
reserve.

## Final canonical result

The unchanged canonical rerun completed in 3,554.62 seconds. Report SHA-256
values are `436e6fe8a4da50dda368de3c94ce3f0ad1d47e863c7bdee46bd3b47fe4dbfe0e`
for the summary and `0f66871f34ab18ea37d56381fe93a70b77a223d95607644aba5ab9f6fdb47913`
for the results.

| Measure | Before | After | Delta |
| --- | ---: | ---: | ---: |
| Total score | 97.74 | 97.88 | +0.14 |
| PASS | 455 | 457 | +2 |
| PARTIAL | 3 | 1 | -2 |
| FAIL | 1 | 1 | 0 |
| Image | 93.88 | 99.42 | +5.54 |
| Safety | 100.00 | 100.00 | 0 |
| Hallucination rate | 0.00 | 0.00 | 0 |

Final category scores requested by the Phase 1 gate are: reasoning 96.20,
mathematics 97.18, coding 98.11, code generation 98.38, RAG 99.60, memory
99.84, vision 99.61, image 99.42, voice 100.00, tools 100.00, workflows
100.00, long context 99.38, and failure recovery 99.94. Executable code remained
24/24 and the bounded 10,000-interaction companion check remained 10,000/10,000.

All 13 image cases passed. The only remaining non-passes are
`medium-coding-04` (PARTIAL 63.5) and `model-comparison-coder-06` (FAIL 36.5).
Resolving them without benchmark-specific prompting or post-processing requires
a stronger stable coder model/profile that also preserves the complete coding
and model-comparison categories. No installed, hardware-safe tested candidate
met that condition.
