# Step 5 — AI Learning, Teacher, and Knowledge OS

Status: **COMPLETE for locally achievable functionality**. The private local
teacher, learning state, knowledge grounding, web/desktop experience, and
native mobile contracts are implemented and verified. No LMS, classroom,
institution, proprietary education service, or pronunciation provider is
reported connected.

## Capability result

| Area | State | Objective evidence |
|---|---|---|
| Teacher and curricula | LOCAL_PASS / RUNTIME_PASS | Owner-scoped five-stage beginner-to-mastery programs, ten teaching modes, persistent preferences, stepwise/example-first prompts, a real admitted local-model lesson, model identity, and matching SHA-256 output passed. |
| Assessment and grading | LOCAL_PASS | Untouched strict five-form assessment artifacts (MCQ, short, long, coding, assignment), progressive hints, exact/rubric grading, answer-digest persistence, attempt bounds, duplicate-mastery denial, and no premature answer disclosure passed. |
| Adaptation and analytics | LOCAL_PASS / RUNTIME_PASS | Mastery, confidence, mistakes, weak topics, streaks, bounded one-level difficulty changes, daily/weekly plans, and required-activity lesson completion persisted in disposable PostgreSQL. |
| Revision and recall | LOCAL_PASS / RUNTIME_PASS | Flashcards, active recall, deterministic spaced scheduling, due-review counts, and revision recommendations passed. |
| Session recovery | LOCAL_PASS / RUNTIME_PASS | A focus session executed start, pause, resume, and complete with interruption count, one-open-session enforcement, owner isolation, and hash-only audit events. |
| Document and RAG teaching | LOCAL_PASS | Only ready owner documents can attach; bounded chunks, untrusted-source delimiting, secret exclusion, preserved citation labels, source digests, foreign-owner denial, and insufficient-safe-source fail-closed behavior passed. |
| Multilingual learning | LOCAL_PASS | Separate instruction/target language tags, mixed-language/Hinglish preference, translation assistance, Unicode assessment/review content, and shared text/voice entry contracts passed. |
| Web and desktop UI | LOCAL_PASS | Learning dashboard, curricula, active sessions, profiles/modes, mastery, weak topics, study plans, source controls, lesson/assessment, hints, grading, revision, and audit paths passed responsive web tests and desktop packaging/launch. |
| Native mobile UI | LOCAL_PASS | Mobile Studio exposes curriculum, lesson, assessment, answer, hint, session, mastery, streak, and review paths through the strict shared API contracts; tests, typecheck, lint, exports, Expo Doctor, and native Android build passed. |
| Acoustic pronunciation scoring | EXTERNAL_BLOCKED | Requires an admitted acoustic scoring runtime or an owner-authorized provider, microphone/device permission, language coverage, privacy policy, and real score validation. Speech transcription is not mislabeled as pronunciation scoring. |
| LMS/classroom/institution services | EXTERNAL_BLOCKED | The existing connector platform can host a future exact-origin/scoped adapter, but no owner account, OAuth consent, provider origin, credentials, classroom, or provider receipt was available. |
| Proprietary exam/certification content | EXTERNAL_BLOCKED | Local preparation works from general knowledge or owner documents. Licensed proprietary question banks require the owner's lawful provider/content authorization. |

## Implemented architecture

- `LearningService` is the single teaching coordinator over the existing
  Teacher Agent, admitted local model router, memory, RAG/document, mission,
  voice, and audit foundations. No parallel agent or voice framework was added.
- Persistent owner-scoped programs now include profiles, languages, goals,
  modes, difficulty, lessons, activities, attempts, skills, document sources,
  review cards, resumable sessions, and learning events.
- Generated lessons and assessments are accepted only after bounded schema,
  hash, model, source, and credential-content checks. Model output is not
  silently repaired before verification.
- Exact answers and learner responses are stored as SHA-256 digests; rubric
  evaluation stores bounded required concepts. Raw answers do not enter the
  learning audit. Every state mutation produces a hash-only event.
- The feature authority now contains **321** capabilities: **263 implemented**,
  **14 runtime-dependent**, **39 external-dependent**, and **5 planned and
  visibly disabled**. It includes **86 implemented Learning OS records** plus
  the explicit external pronunciation-scoring record.

## Database and migration

Migration `0021_learning_knowledge_os` is the schema head. It non-destructively
extends the existing learning tables and creates owner-scoped skill, source,
session, and event tables. Clean upgrade `0001 -> 0021`, exact ORM comparison,
Alembic drift check, downgrade normalization for new activity/partial-score
states, downgrade, and re-upgrade all passed on disposable PostgreSQL. No
production database was reset or truncated.

## Security result

- Owner checks exist in queries and composite foreign keys; wrong-owner reads
  and writes return the same not-found boundary.
- The Teacher Agent remains local-only with model inference and no connector,
  filesystem, terminal, browser, tool, or unrestricted network permission.
- Document chunks are bounded untrusted data. Prompt-injection text cannot
  grant instructions/tools, and missing or invented source labels fail closed.
- Recognizable credentials are rejected from persisted learning text and
  generated artifacts. Prompts, raw source text, raw answers, and model output
  are absent from learning audit metadata and API error details.
- Security and secret scans passed with **0 critical** and **0 high** findings.
  The dependency audit retains **14 known moderate** transitive advisories in
  Expo build tooling; available forced fixes are breaking/incompatible and no
  vulnerable code was promoted to a runtime capability.

## Verification

- Focused Step 5 backend/migration/registry: **42 passed**, **4 intentional
  PostgreSQL-environment skips**, zero failed.
- Complete backend: **3,003 passed**, **53 intentional environment/runtime
  skips**, zero failed.
- Complete web: **201 passed**; typecheck, lint, and production build passed.
- Complete mobile: **64 passed**; shared/mobile typecheck, lint, Android/iOS
  static exports, and Expo Doctor **21/21** passed.
- Disposable PostgreSQL: **53 passed** through `0021`; no schema drift.
- Browser/PWA E2E, native Android debug packaging, desktop Rust/build/package/
  launch, private gateway/service checks, security audit, and release gate
  passed.
- Full local runtime E2E passed. The Learning probe executed a real local-model
  lesson and verified persistent attempts, three required assessments,
  difficulty adaptation, mastery analytics, one spaced review, session
  interruption/recovery, owner isolation, memory use, and hash-only audit.

## Exact external owner actions

1. For pronunciation scoring, select an eligible acoustic runtime or lawful
   provider, authorize microphone/device access, configure its exact origin and
   minimum scopes if external, and validate real scores per supported language.
2. For LMS/classroom/institution use, choose the provider, create/own the
   account, register the exact callback/origin, complete OAuth consent/MFA, and
   approve minimum course/roster/assignment scopes before any real smoke test.
3. Supply a lawful content license/provider grant before proprietary
   certification or exam-bank content can be represented as available.

Machine-readable evidence is in `reports/STEP5_LEARNING_EVIDENCE.json`.
Step 6 was not started.
