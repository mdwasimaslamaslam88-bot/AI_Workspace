import ssl
from unittest.mock import Mock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.clients.postgres import build_postgres_connect_args, create_postgres_engine
from app.core.config import Settings


@patch("app.clients.postgres.ssl.create_default_context")
@patch("app.clients.postgres.create_async_engine")
def test_database_engine_configuration_is_forwarded_without_connecting(
    create_engine, create_default_context
):
    engine = Mock(spec=AsyncEngine)
    ssl_context = Mock(spec=ssl.SSLContext)
    create_engine.return_value = engine
    create_default_context.return_value = ssl_context
    configured = Settings(
        _env_file=None,
        DATABASE_URL="postgresql+asyncpg://db.example.test/workspace",
        DATABASE_CONNECT_TIMEOUT_SECONDS=1.5,
        DATABASE_POOL_TIMEOUT_SECONDS=2.5,
        DATABASE_COMMAND_TIMEOUT_SECONDS=3.5,
        DATABASE_POOL_SIZE=2,
        DATABASE_MAX_OVERFLOW=3,
        DATABASE_SSL_MODE="verify-full",
        DATABASE_SSL_ROOT_CERT="/run/secrets/postgresql-ca.pem",
    )

    assert create_postgres_engine(configured) is engine
    create_default_context.assert_called_once_with(
        cafile="/run/secrets/postgresql-ca.pem"
    )
    assert ssl_context.check_hostname is True
    assert ssl_context.verify_mode == ssl.CERT_REQUIRED
    create_engine.assert_called_once_with(
        str(configured.DATABASE_URL),
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=3,
        pool_timeout=2.5,
        connect_args={
            "timeout": 1.5,
            "command_timeout": 3.5,
            "ssl": ssl_context,
        },
    )


def test_database_ssl_defaults_to_verified_tls(monkeypatch):
    monkeypatch.delenv("DATABASE_SSL_MODE", raising=False)

    assert Settings(_env_file=None).DATABASE_SSL_MODE == "verify-full"


@patch("app.clients.postgres.create_async_engine")
def test_database_ssl_can_be_explicitly_disabled_for_local_development(create_engine):
    engine = Mock(spec=AsyncEngine)
    create_engine.return_value = engine
    configured = Settings(
        _env_file=None,
        DATABASE_URL="postgresql+asyncpg://localhost/workspace",
        DATABASE_SSL_MODE="disable",
    )

    assert create_postgres_engine(configured) is engine
    assert create_engine.call_args.kwargs["connect_args"]["ssl"] is False
    assert "sslrootcert" not in create_engine.call_args.kwargs["connect_args"]


@patch("app.clients.postgres.ssl.create_default_context")
def test_verify_ca_checks_certificate_without_checking_hostname(
    create_default_context,
):
    ssl_context = Mock(spec=ssl.SSLContext)
    create_default_context.return_value = ssl_context
    configured = Settings(
        _env_file=None,
        DATABASE_SSL_MODE="verify-ca",
        DATABASE_SSL_ROOT_CERT="/run/secrets/postgresql-ca.pem",
    )

    connect_args = build_postgres_connect_args(configured)

    assert connect_args["ssl"] is ssl_context
    create_default_context.assert_called_once_with(
        cafile="/run/secrets/postgresql-ca.pem"
    )
    assert ssl_context.check_hostname is False
    assert ssl_context.verify_mode == ssl.CERT_REQUIRED


@patch("app.clients.postgres.ssl.SSLContext")
def test_require_uses_tls_without_certificate_verification(create_ssl_context):
    ssl_context = Mock()
    create_ssl_context.return_value = ssl_context
    configured = Settings(_env_file=None, DATABASE_SSL_MODE="require")

    connect_args = build_postgres_connect_args(configured)

    assert connect_args["ssl"] is ssl_context
    create_ssl_context.assert_called_once_with(ssl.PROTOCOL_TLS_CLIENT)
    assert ssl_context.check_hostname is False
    assert ssl_context.verify_mode == ssl.CERT_NONE


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("DATABASE_CONNECT_TIMEOUT_SECONDS", 0),
        ("DATABASE_POOL_TIMEOUT_SECONDS", 0),
        ("DATABASE_COMMAND_TIMEOUT_SECONDS", 0),
        ("DATABASE_POOL_SIZE", 0),
        ("DATABASE_MAX_OVERFLOW", -1),
        ("DATABASE_SSL_MODE", "prefer"),
    ],
)
def test_database_configuration_bounds_are_validated(field, value):
    with pytest.raises(ValueError):
        Settings(_env_file=None, **{field: value})
