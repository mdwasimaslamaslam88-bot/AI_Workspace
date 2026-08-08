import runpy
import ssl
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, Mock

from sqlalchemy import pool
import sqlalchemy.ext.asyncio as sqlalchemy_asyncio

from app.clients import postgres as postgres_client


def test_alembic_online_engine_uses_shared_postgres_connection_policy(monkeypatch):
    database_url = "postgresql+asyncpg://db.example.test/workspace"
    ca_path = "/run/secrets/postgresql-ca.pem"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("DATABASE_CONNECT_TIMEOUT_SECONDS", "1.5")
    monkeypatch.setenv("DATABASE_COMMAND_TIMEOUT_SECONDS", "3.5")
    monkeypatch.setenv("DATABASE_SSL_MODE", "verify-full")
    monkeypatch.setenv("DATABASE_SSL_ROOT_CERT", ca_path)

    ssl_context = Mock(spec=ssl.SSLContext)
    create_default_context = Mock(return_value=ssl_context)
    monkeypatch.setattr(
        postgres_client.ssl, "create_default_context", create_default_context
    )

    connection = AsyncMock()
    connection_manager = AsyncMock()
    connection_manager.__aenter__.return_value = connection
    engine = Mock()
    engine.connect.return_value = connection_manager
    engine.dispose = AsyncMock()
    create_async_engine = Mock(return_value=engine)
    monkeypatch.setattr(
        sqlalchemy_asyncio, "async_engine_from_config", create_async_engine
    )

    alembic_config = Mock()
    alembic_config.config_file_name = None
    alembic_config.config_ini_section = "alembic"
    alembic_config.get_section.return_value = {}
    alembic_context = Mock()
    alembic_context.config = alembic_config
    alembic_context.is_offline_mode.return_value = False
    alembic_module = ModuleType("alembic")
    alembic_module.context = alembic_context
    monkeypatch.setitem(sys.modules, "alembic", alembic_module)

    env_path = Path(__file__).parents[2] / "migrations" / "env.py"
    runpy.run_path(str(env_path), run_name="__alembic_env_test__")

    create_default_context.assert_called_once_with(cafile=ca_path)
    assert ssl_context.check_hostname is True
    assert ssl_context.verify_mode == ssl.CERT_REQUIRED
    create_async_engine.assert_called_once_with(
        {"sqlalchemy.url": database_url},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={
            "timeout": 1.5,
            "command_timeout": 3.5,
            "ssl": ssl_context,
        },
    )
    engine.connect.assert_called_once_with()
    connection.run_sync.assert_awaited_once()
    engine.dispose.assert_awaited_once_with()
    alembic_context.run_migrations.assert_not_called()
