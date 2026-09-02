# Persistent AI Teacher and learning system

AI OS provides an authenticated, owner-scoped learning workspace at
`/api/v1/learning`. The web/desktop Learning panel and mobile Studio use the
same strict response contracts and persistent PostgreSQL records.

## Implemented learning flow

```text
owner subject and goal
→ five-stage beginner-to-mastery curriculum
→ verified local-model lesson
→ exercise, quiz, conversation, or revision practice
→ exact normalized answer verification
→ persisted progress and adaptive difficulty
→ deterministic spaced-repetition review
```

Each curriculum begins with Foundations, Core skills, Applied practice,
Independent use, and Mastery review. Difficulty progresses from the selected
starting level to the target level. A lesson becomes ready only after the
existing Agent OS has independently verified the untouched local-model output
and its SHA-256 digest. The persisted lesson records the exact output, digest,
model identity, generation time, objectives, and the IDs of any owner memories
used for teaching-style adaptation.

Practice supports exercise, quiz, conversation, and revision modes. Expected
answers are normalized with Unicode NFKC, case folding, and whitespace
normalization, then stored only as SHA-256 digests. An incorrect attempt does
not reveal the answer or explanation until the configured attempt budget is
exhausted. Correct attempts update lesson completion, program progress,
accuracy, and the bounded adaptive-difficulty state.

Spaced repetition is local and persistent. Review cards use a deterministic,
bounded SM-2-style schedule driven by owner-submitted quality values from zero
through five. Duplicate fronts within one program are rejected as a safe
conflict. The scheduler does not claim to assess pronunciation or infer review
quality automatically.

## Memory, multilingual use, and isolation

Lesson generation may retrieve up to four memories from the authenticated
owner to adapt teaching style. Retrieved memory is framed as private,
non-instructional data and cannot grant tool permissions. The learning agent
has only `model_inference`; it has no terminal, filesystem, browser, connector,
or arbitrary network authority.

Private memory is supplied only through the Agent OS's fail-closed local-only
model selection contract. The learning request disables External AI selection
even when a fallback provider is configured; if no admitted local model remains,
generation fails rather than allowing private memory to cross that boundary.
Programs, lessons, activities, attempts, and review cards use
owner-scoped composite database constraints, and foreign-owner lookups return
the same not-found response as absent records.

Instruction and target language tags are stored independently, so lessons can
teach one language through another and accept mixed Unicode answers. This is a
content-language contract; it is not evidence of acoustic pronunciation
accuracy.

## Explicit external boundary

Pronunciation scoring remains `external_dependency`. AI OS exposes that status
in the authenticated capability response and in both clients. Making it live
requires a legitimately configured acoustic pronunciation runtime or provider,
microphone permission, owner authorization, language/model coverage, privacy
policy, and end-to-end scoring validation. The current system never converts
speech transcription or local text comparison into a fabricated pronunciation
score.

## Verification

Automated evidence covers curriculum progression, verified-output integrity,
answer normalization, no answer-key leakage, adaptive progress, deterministic
review scheduling, API error containment, strict web/mobile response parsing,
web and mobile interaction paths, exact Alembic/ORM parity, PostgreSQL
persistence, owner isolation, composite foreign-key enforcement, and a real
local-model lesson smoke against a disposable database. The runtime smoke also
confirms private preferences are used locally without appearing in logs or an
External AI route.

The 2026-09-03 release gate measured 2,911 backend tests passed (46 intentional
environment/runtime skips), 186 web tests passed, 60 mobile tests passed, Expo
Doctor 21/21, desktop Rust tests 2/2, and 46 disposable-PostgreSQL tests passed
with migration `0016` and no schema drift. The separate full runtime E2E passed
the real local teacher, progress/adaptation, and spaced-repetition sentinels.
