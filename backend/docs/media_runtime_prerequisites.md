# Local media runtime prerequisites

Environment audit recorded on 2026-08-22 for this workstation:

- GPU: NVIDIA GeForce RTX 3060 with 12,288 MiB VRAM; driver 580.173.02.
- Ollama loopback service: available, with `nomic-embed-text:latest`,
  `qwen2.5-coder:7b`, and `qwen3:8b`. None is an image-generation,
  image-editing, speech-to-text, or text-to-speech model.
- Image services: no ComfyUI service on port 8188, no Automatic1111-compatible
  service on port 7860, no related executable or Python runtime, and no cached
  image checkpoint in the repository or Hugging Face cache.
- Voice services: no `ffmpeg`/`ffprobe`, Whisper/whisper.cpp/faster-whisper,
  Piper, Coqui TTS, speech model, or local voice model was installed.

Therefore image generation, image editing, and voice real-runtime validation
are blocked by external runtime/model prerequisites; no mock adapter is enabled.
To unblock image generation, install a loopback-only, deadline/cancellation-aware
local image runtime plus an explicitly allowlisted text-to-image checkpoint that
fits the 12 GiB GPU. Image editing additionally requires an allowlisted
image-to-image/inpainting checkpoint and bounded workflow. To unblock voice,
install bounded audio probing/decoding (FFmpeg), one local STT runtime and model
(such as whisper.cpp or faster-whisper), and one local TTS runtime and voice
(such as Piper). All selected licenses and model files remain an operator choice.

After installation, runtime adapters still need implementation and authenticated
real-runtime tests before these capabilities may be advertised. The application
must not infer availability merely from GPU presence.
