# AI Learning, Teacher, and Knowledge OS

AI OS exposes an authenticated, owner-scoped learning workspace through
`/api/v1/learning`. The web/desktop Learning panel and native mobile Studio use
the same strict shared contracts. This is an extension of the existing Agent
OS, memory, RAG, document-ingestion, model-routing, and voice foundations; it is
not a parallel agent or speech stack.

## Coherent teaching lifecycle

```text
learner goal and level
→ five-stage beginner-to-mastery curriculum
→ verified local-model lesson
→ comprehension check and progressive hints
→ exact or rubric-based assessment
→ mastery, confidence, mistakes, and difficulty update
→ spaced review and daily/weekly recommendation
→ resumable next session
```

A program retains subject, long-term goal, teaching/content languages, current
and target difficulty, weekly time budget, teaching mode, safe preferences,
streaks, lessons, skills, attempts, review cards, attached knowledge sources,
and session history. Curricula advance through Foundations, Core skills,
Applied practice, Independent use, and Mastery review. Lesson difficulty is
deterministically distributed between the chosen start and target levels.

The supported modes are teacher, Socratic, coach, mentor, interviewer,
pair-programming, study, focus, exam, and revision. A lesson prompt asks for an
age- and difficulty-appropriate step-by-step explanation, worked example,
compact text concept map, guided and independent practice, and revision
summary. Different teaching/content language tags enable multilingual and
translation-assisted teaching; the mixed-language preference enables flows
such as Hinglish without changing the grading or security contract.

The existing AI Presence can carry spoken or typed learning conversations and
the existing mission path retains its real listening/thinking/working states.
Persistent curriculum, grading, and revision changes occur only through the
learning API. Voice transcription is not treated as pronunciation scoring.

## Assessment and adaptation

Verified assessment generation requires an untouched strict JSON artifact with
exactly one MCQ, short-answer, long-answer, coding, and assignment activity.
The artifact must pass schema, bounds, kind order, hint, rubric, source, model,
and SHA-256 checks before persistence. It is never repaired or rewritten after
generation. Coding and assignment answers use deterministic rubric concepts;
learner code is not silently executed by the Learning service. Executable code
practice belongs in the existing sandboxed Coding workspace and verifier.

Exact activities store only the normalized answer SHA-256. Rubric activities
store bounded required concepts and derive a reproducible score. Raw learner
answers are never retained. Feedback does not expose an answer before the
attempt budget is exhausted. Every attempt updates the corresponding skill's
mastery, evidence-count confidence, mistake count, and next-review time. Three
recent verified scores can move adaptive difficulty by at most one level and
never beyond the configured bounds.

Progressive hints are stateful and bounded. Required activities determine
lesson completion; optional practice does not block progression. Flashcards
use a deterministic bounded SM-2-style interval. Daily and weekly plans prefer
weak topics and unfinished lessons. Mastery, confidence, due reviews, streaks,
weak topics, and the open session are returned by the analytics contract.

Only one active/paused study session may exist for a program. Pause, resume,
and completion are explicit owner-scoped transitions. Interruption count and
last activity time support recovery from an interrupted teaching conversation.

## Document-grounded teaching

Only already-ingested, ready documents owned by the current user can be
attached. AI OS snapshots their document/asset identity and content digest,
then retrieves at most four owner-scoped chunks through the existing RAG data
model. Selection is deterministic and lexical; provider-specific assumptions
do not enter the learning core.

Source excerpts are delimited as untrusted data and explicitly cannot grant
instructions or tools. Obvious credential-bearing chunks are excluded. A
program with sources but no safe excerpt fails closed. Grounded lesson and
assessment outputs must preserve a verified source label; missing or invented
labels reject the artifact. The UI distinguishes `source_grounded` from
`general_knowledge`, exposes source counts and integrity digests, and permits
controlled detachment.

TXT, PDF, DOCX, CSV, code, and transcript inputs use the existing document and
media ingestion paths. “Transcript to learning” means a locally produced or
owner-supplied transcript is ingested as a document; it does not claim a new
external transcription provider. Unsupported source facts produce an explicit
insufficient-source failure or uncertainty instruction rather than fabricated
document content.

## Security and audit model

- Every query and composite foreign key is owner scoped. Foreign-owner and
  absent resources return the same not-found boundary.
- The Teacher Agent receives only `model_inference`, uses admitted local models,
  disables External AI fallback, and has no filesystem, terminal, browser,
  connector, or network permission.
- Source and preference blocks are data, not instructions. Generated output is
  independently verified and checked for credential-like content before it is
  stored.
- User-authored learning text rejects recognizable bearer/API/client-secret,
  private-key, AWS-key, and OpenAI-style credential forms. Credentials belong
  in the encrypted provider vault, never learning memory.
- Audit rows retain action, entity identifiers, timestamp, and a canonical
  metadata SHA-256. Raw answers, prompts, source text, credentials, and model
  output are absent from audit metadata.
- Program, lesson, activity, review, source, session, skill, and event counts
  are bounded. Inputs, output schemas, attempts, hints, retries, and model
  deadlines are bounded.

## API contract

- `GET /api/v1/learning/capabilities`
- `GET|POST /api/v1/learning/programs`
- `GET /api/v1/learning/programs/{program_id}`
- `PUT /api/v1/learning/programs/{program_id}/profile`
- `POST /api/v1/learning/programs/{program_id}/lessons/{lesson_id}/generate`
- `POST /api/v1/learning/programs/{program_id}/lessons/{lesson_id}/assessment`
- `POST /api/v1/learning/programs/{program_id}/lessons/{lesson_id}/activities`
- `POST /api/v1/learning/programs/{program_id}/activities/{activity_id}/hint`
- `POST /api/v1/learning/programs/{program_id}/activities/{activity_id}/attempts`
- `POST /api/v1/learning/programs/{program_id}/review-items`
- `POST /api/v1/learning/programs/{program_id}/review-items/{item_id}/reviews`
- `POST|DELETE /api/v1/learning/programs/{program_id}/sources[/{source_id}]`
- `POST /api/v1/learning/programs/{program_id}/sessions`
- `POST /api/v1/learning/programs/{program_id}/sessions/{session_id}/{pause|resume|complete}`
- `GET /api/v1/learning/programs/{program_id}/analytics`
- `GET /api/v1/learning/programs/{program_id}/study-plan?days=1..7`
- `GET /api/v1/learning/programs/{program_id}/audit?limit=1..100`

## UI

The Learning workspace provides curriculum creation, active-program selection,
mastery/confidence/streak summaries, session controls, teaching preferences,
source attachment, daily/weekly plans, weak-topic analysis, lessons,
assessments, hints, grading, flashcards, revision, and hash-only audit history.
It inherits the shared responsive tokens, light/dark/system themes, keyboard and
screen-reader behavior. Native mobile Studio exposes curriculum, lesson,
assessment, answer, hint, session, mastery, streak, and spaced-review paths
using the same contracts.

## Persistence and migration

Migration `0021_learning_knowledge_os` extends the non-destructive `0016`
learning schema with teaching profiles, richer activities and attempts,
owner-scoped skills, document sources, resumable sessions, and hash-only events.
The migration has an exact ORM match, supports full clean upgrade, downgrade,
and re-upgrade, and safely maps new activity/partial-score states when
downgrading to the older schema.

## Truthful boundaries

Pronunciation scoring is `EXTERNAL_BLOCKED`: it requires an admitted acoustic
scoring runtime or an owner-authorized provider, microphone permission,
language/model coverage, privacy controls, and real scoring validation. No LMS,
classroom, institution, proprietary certification-content, telephony, or cloud
education account is claimed connected. Those integrations require a specific
owner account, exact provider origin/scopes, consent, credentials, and provider
evidence. All independent local Learning OS behavior remains available without
them.

The machine-readable evidence is `reports/STEP5_LEARNING_EVIDENCE.json`; the
human-readable gate is `reports/STEP5_LEARNING_OS.md`.
