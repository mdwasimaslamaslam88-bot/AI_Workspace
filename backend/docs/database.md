# Database persistence foundation

The backend uses SQLAlchemy 2.x async ORM with the existing
`postgresql+asyncpg://` URL contract. `DATABASE_URL` is optional for local
runtime tests; production deployments should provide it through a secret
manager or environment injection, never source control.

Configuration:

- `DATABASE_CONNECT_TIMEOUT_SECONDS`: asyncpg connection timeout.
- `DATABASE_POOL_TIMEOUT_SECONDS`: maximum SQLAlchemy pool checkout wait.
- `DATABASE_COMMAND_TIMEOUT_SECONDS`: asyncpg command/query timeout.
- `DATABASE_POOL_SIZE`: bounded persistent connection pool size.
- `DATABASE_MAX_OVERFLOW`: bounded temporary overflow connections.
- `DATABASE_SSL_MODE`: explicit asyncpg TLS mode. The application default is
  `verify-full`; local development may explicitly select `disable`.
- `DATABASE_SSL_ROOT_CERT`: optional path to a mounted CA certificate for
  `verify-ca` or `verify-full`; certificates are not stored in source control.
- `TEST_DATABASE_URL`: optional URL for a dedicated disposable test database.
  It must never point at development or production data.
- `RUN_DATABASE_INTEGRATION_TESTS=true`: explicit opt-in required before tests
  may use `TEST_DATABASE_URL`.

FastAPI lifespan creates the engine and async session factory. Request
handlers obtain sessions through `app.db.dependencies.get_db_session`.
Session scopes manage session lifetime, rollback on failure, cleanup, and
connection release. They never commit automatically. Repositories receive a
session and never commit. Service, unit-of-work, or other application
transaction logic owns successful transactions and must explicitly call
`await session.commit()`.

Process-lifetime dependencies are acquired through one FastAPI lifespan cleanup
stack. Each existing closer is registered immediately after its resource is
constructed, so a later startup failure disposes every resource already
acquired. Normal shutdown attempts Ollama, Redis, and PostgreSQL cleanup in
reverse acquisition order, and a failure from one closer does not prevent the
remaining closers from running.

Alembic is configured in `alembic.ini` and `migrations/`. Normal Alembic
commands load `DATABASE_URL` only when explicitly run. When
`RUN_DATABASE_INTEGRATION_TESTS=true`, Alembic instead requires and uses
`TEST_DATABASE_URL` without falling back to `DATABASE_URL`. No migration runs
during application startup. The approved domain metadata currently defines
the `users`, `conversations`, and `messages` tables. Alembic imports the
`app.models` registry before reading `Base.metadata`; future mapped-model
modules must be imported by that registry.

The `messages.content` column remains PostgreSQL `TEXT`, with one durable
semantic invariant for system, user, and assistant Messages:
`char_length(content) <= 100000`. Alembic revision
`0003_bound_message_content` follows `0002_user_access_credential` and adds only
the named `ck_messages_content_length_bounded` CHECK constraint. The migration
does not rewrite, truncate, or normalize rows. If pre-existing content violates
the invariant, PostgreSQL rejects the migration transaction instead of altering
that content. Application services reject oversized content before persistence,
repositories repeat the check before sequence allocation, and PostgreSQL is the
final authority for direct or bypass writes.

Public Message history pagination applies a separate 100,000-character
cumulative page budget without changing the durable per-Message invariant.
`MessageRepository.list_for_owner()` uses one owner-scoped SQL statement: a
sequence-ordered candidate CTE inspects at most `limit + 1` rows and selects
only Message IDs, sequence numbers, and PostgreSQL `char_length(content)`; a
window sum finds the longest whole-Message prefix within the page budget; and
the final join materializes complete ORM Messages only for that prefix. The
same owner and Conversation filters are enforced in both the candidate and
final stages. Count- or character-limited pages use the final returned sequence
as the existing keyset cursor, so the unique `(conversation_id,
sequence_number)` constraint and ascending index access remain sufficient.
Public history and internal generation-context reads remain separate.

The internal generation-context query has its own non-paginated, SQL-gated
snapshot boundary. A candidate CTE inspects at most 101 owner-scoped rows in
ascending sequence order and selects only Message ID, sequence number, and
`char_length(content)`. Aggregate metadata determines the complete candidate
count, cumulative characters, and final candidate sequence. Only when the
complete context contains at most 100 Messages and 100,000 characters does a
second stage in the same SQL statement project role, content, and sequence.
Oversized contexts return metadata without any content rows, allowing the
application to preserve final-sequence race precedence while avoiding partial
generation or ORM Message materialization. This remains one database round trip
and does not change the public history CTE, transaction ownership, schema, or
the existing `(conversation_id, sequence_number)` index.

## PostgreSQL integration tests

The default `python -m pytest -q` run remains database-free. Real PostgreSQL
tests require both `RUN_DATABASE_INTEGRATION_TESTS=true` and a
`TEST_DATABASE_URL` targeting `127.0.0.1/ai_workspace_test`. The harness
rejects any other host or database and never falls back to `DATABASE_URL`.

To load the existing `.env` for one test process while blanking
`DATABASE_URL` from that process, run:

```bash
python tests/db/run_postgres_integration.py
```

The runner executes:

```bash
python -m pytest -q -m integration tests/db/test_postgres_integration.py
```

The session fixture uses the existing Alembic chain to downgrade to `base`,
upgrade to `head`, run the focused tests, and downgrade to `base` again
during cleanup. It does not create schema objects from ORM metadata.
