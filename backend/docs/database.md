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

Alembic is configured in `alembic.ini` and `migrations/`. It loads
`DATABASE_URL` only when an Alembic command is explicitly run. No migration
runs during application startup, and no domain models or application tables
are defined by this foundation milestone. Alembic imports the `app.models`
registry before reading `Base.metadata`; future mapped-model modules must be
imported by that registry.
