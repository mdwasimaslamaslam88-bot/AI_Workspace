# Creative experiences

WORK STATION provides owner-scoped interactive stories, text games, and
explicitly fictional character experiences at `/api/v1/creative`. Creating an
experience stores its bounded premise and mode but creates no output. A turn is
reported only after the local-only Agent OS route returns an untouched response
whose verifier evidence contains the same SHA-256 digest persisted with the
model identifier.

## Implemented lifecycle

The web/desktop Creative panel and mobile Workspaces screen use the same typed
API contract:

```text
create active experience
→ submit owner turn
→ local Agent OS generation
→ independent output verification
→ persist exact artifact and evidence
→ continue, or complete
```

Experiences are limited to 20 per owner and 100 turns each. Inputs, prompts,
history, outputs, and model evidence have fixed size limits. Generation receives
only `model_inference`, disables External AI fallback, and has a 120-second
deadline with one bounded retry. A concurrent turn, a completed experience, or
an unavailable verified local route fails without fabricating progress.

PostgreSQL composite owner foreign keys and owner-filtered repositories isolate
both experiences and turns. API errors and normal audit logs omit premise,
turn, output, and model failure details. Clients reject malformed turn order,
turn-count disagreement, invalid verification hashes, and capability responses
that claim unavailable media systems are ready.

## Safety and capability boundary

The local creative workspace is general-audience only. A fixed, Unicode-
normalized pre-generation gate rejects explicit sexual, non-consensual,
exploitative, grooming, and minor-related sexual requests; the same gate checks
model output before persistence. It is defense in depth rather than a universal
content classifier.

Image generation/editing and voice use their existing separately admitted
runtime routes. Video, animation, generative audio creation/editing, and
protected adult experiences remain `external_dependency`. They are not marked
ready without a separately verified media runtime and, where applicable,
jurisdiction, age-verification, and consent controls. No output from this text
workspace is presented as a video, audio, image, tool action, call, or real-
world event.

## Verification

Focused tests cover lifecycle, safety, owner isolation, response redaction,
schema parity, migration downgrade, strict web/mobile decoding, and UI paths.
The disposable PostgreSQL suite validates real foreign keys and persistence.
`python -m scripts.real_creative_smoke` additionally exercises one admitted
local model and validates the persisted output digest. Its evidence output
includes only the public model identifier and SHA-256—not the premise, input,
generated content, prompt, owner data, or credentials. It requires the normal
local runtime and the disposable-database environment used by the release gate.

The current locally achievable completion record is
`reports/STEP6_CREATIVE_ENTERTAINMENT.md`; machine-readable evidence is
`reports/STEP6_CREATIVE_EVIDENCE.json`.
