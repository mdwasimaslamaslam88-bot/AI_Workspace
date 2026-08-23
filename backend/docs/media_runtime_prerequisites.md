# Local media runtime inventory

Workstation inventory recorded on 2026-08-23:

- NVIDIA GeForce RTX 3060, 12,288 MiB VRAM, driver 580.173.02;
- approximately 78 GiB system RAM;
- private asset storage outside the source tree with owner-only traversal;
- Ollama 0.32.5 on loopback with exact allowlisting for `qwen3:8b`,
  `qwen2.5-coder:7b`, `nomic-embed-text:latest`, and `qwen2.5vl:7b`;
- `qwen2.5vl:7b` reports completion and vision capabilities from Ollama model
  metadata and passed a real authenticated PNG/CUDA smoke;
- an isolated FFmpeg n8.1.2 static GPL build with working `ffmpeg` and
  `ffprobe` executables;
- faster-whisper 1.2.1 with CTranslate2 4.8.1 and the pinned
  `Systran/faster-whisper-small.en` model at commit
  `d1d751a5f8271d482d14ca55d9e2deeebbae577f`;
- Piper 1.7.0 with only the pinned `en_US-lessac-medium` voice at commit
  `f5a6e9094787fd865d65cb024472f977f9c542b5`;
- ComfyUI v0.33.1 at commit
  `72865f4f27eaf5396f8f36370e0a2be3a9a090ee`, in a separate Python
  environment with PyTorch 2.11.0+cu130, with the official Stable Diffusion XL
  Base 1.0 checkpoint at commit
  `462165984030d82259a11f4367a4eed129e94a7b`.

The model binaries are integrity pinned. The faster-whisper `model.bin` SHA-256
is `62b2a45b05ee59acb4a5341b33ee35e041395d378d418a18acfe4c9e768ee37a`.
The Piper ONNX SHA-256 is
`5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f`.
The SDXL checkpoint SHA-256 is
`31e35c80fc4829d14f90153f4c74cd59c90b779f6afe05a74cd6120b893f7e5b`.
All runtime/model directories are outside Git and deny group/other access.

The isolated FFmpeg build passed a real bounded WAV-to-Opus conversion and an
independent `ffprobe` metadata check. An OS-package installation under
`/usr/bin` remains unavailable without interactive sudo authority; the verified
isolated executables satisfy the application/runtime boundary without replacing
system multimedia libraries.

The authenticated voice smoke synthesized a real 22,050 Hz mono WAV locally,
uploaded it through private `AssetStorage`, transcribed it with CUDA, recovered
the spoken `amber` and `lantern` checkpoints, observed 1,296 MiB process GPU
memory and nonzero GPU utilization, rejected a foreign owner, tombstoned both
assets, and left no audio bytes, transcript, text input, or storage path in
application logs. The final release rerun observed 1,297 MiB and 21 percent GPU
utilization. Both model inference paths run offline after installation.

ComfyUI is restricted to `127.0.0.1`, one active request, low-VRAM mode, 1.5 GiB
VRAM reserve, no custom nodes, no API nodes, no browser launch, disabled PNG
metadata, bounded uploads, and cache-free workflows. The application submits
only its fixed txt2img/img2img/inpainting graphs and asks ComfyUI to unload the
model and release memory after each terminal path. The selected FP16 SDXL model
has a conservative 9 GiB VRAM admission requirement and therefore fits the
current 12 GiB device with headroom; larger image models are not installed.
The default shared GPU admission capacity is one across Ollama generation,
ComfyUI generation/editing, and CUDA speech recognition, preventing overlapping
large model allocations on this card.

Real validation is performed by `scripts/real_voice_smoke.py` and
`scripts/real_image_smoke.py` from the backend directory against only the
approved disposable PostgreSQL database. The image smoke owns the loopback
ComfyUI process, requires NVIDIA process-memory/utilization evidence, exercises
generation, img2img, and mask-based inpainting, proves a new identity and exact
source provenance for both edits, checks authenticated media delivery and owner
isolation, deletes all generated assets/runtime files, and requires shutdown
without a forced kill. The real 768-by-768 SDXL run completed all three paths,
observed 8,392 MiB peak ComfyUI process memory and 100 percent GPU utilization,
and passed every cleanup and clean-shutdown assertion.

These installations do not make availability unconditional. If a local process
is stopped, a configured file disappears, or hardware admission changes, the
catalog and capability diagnostics fail closed. Future hardware upgrades only
require process restart, inventory re-detection, and installation/configuration
of another exact runtime model; the API, database, storage, and frontend media
contracts remain unchanged.
