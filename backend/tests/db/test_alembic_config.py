import runpy
import ssl
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from sqlalchemy import pool
import sqlalchemy.ext.asyncio as sqlalchemy_asyncio

from app.clients import postgres as postgres_client


def test_alembic_online_engine_uses_shared_postgres_connection_policy(monkeypatch):
    database_url = "postgresql+asyncpg://db.example.test/workspace"
    ca_path = "/run/secrets/postgresql-ca.pem"
    monkeypatch.delenv("RUN_DATABASE_INTEGRATION_TESTS", raising=False)
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


def _run_offline_alembic_env(monkeypatch):
    alembic_config = Mock()
    alembic_config.config_file_name = None
    alembic_context = MagicMock()
    alembic_context.config = alembic_config
    alembic_context.is_offline_mode.return_value = True
    alembic_module = ModuleType("alembic")
    alembic_module.context = alembic_context
    monkeypatch.setitem(sys.modules, "alembic", alembic_module)

    env_path = Path(__file__).parents[2] / "migrations" / "env.py"
    runpy.run_path(str(env_path), run_name="__alembic_env_test__")
    return alembic_context


def test_alembic_integration_mode_uses_only_test_database_url(monkeypatch):
    runtime_url = "postgresql+asyncpg://db.example.test/workspace"
    test_url = "postgresql+asyncpg://db.example.test/workspace_test"
    monkeypatch.setenv("RUN_DATABASE_INTEGRATION_TESTS", "true")
    monkeypatch.setenv("DATABASE_URL", runtime_url)
    monkeypatch.setenv("TEST_DATABASE_URL", test_url)

    alembic_context = _run_offline_alembic_env(monkeypatch)

    configured_url = alembic_context.configure.call_args.kwargs["url"]
    assert configured_url == test_url
    assert configured_url != runtime_url
    alembic_context.run_migrations.assert_called_once_with()


def test_alembic_integration_mode_never_falls_back_to_database_url(monkeypatch):
    monkeypatch.setenv("RUN_DATABASE_INTEGRATION_TESTS", "true")
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://db.example.test/workspace"
    )
    monkeypatch.setenv("TEST_DATABASE_URL", "")

    engine = Mock()
    engine.connect = Mock()
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

    with pytest.raises(RuntimeError, match="TEST_DATABASE_URL must be configured"):
        runpy.run_path(str(env_path), run_name="__alembic_env_test__")

    create_async_engine.assert_not_called()
    engine.connect.assert_not_called()
    alembic_context.run_migrations.assert_not_called()
