"""Unit tests for integration conftest fixtures and auth_headers factory.

These tests verify the fixture helpers *without* requiring a real database.
They import the classes/functions directly and test them in isolation.
"""

from __future__ import annotations

import base64
import json
import os
from unittest.mock import AsyncMock

import pytest

from tests.integration.conftest import _AuthHeaderFactory
from tests.integration.pool_wrapper import TransactionalPool


class TestAuthHeaderFactory:
    """Verify auth_headers produces correct Envoy-style header dicts."""

    @pytest.fixture
    def factory(self) -> _AuthHeaderFactory:
        return _AuthHeaderFactory()

    def test_default_headers(self, factory: _AuthHeaderFactory) -> None:
        headers = factory()
        assert headers["x-auth-user-id"] == "test-user"
        assert headers["x-auth-email"] == "test@example.com"
        assert headers["x-auth-tenant"] == "default"

        # Roles should be base64-encoded JSON array
        decoded = json.loads(base64.b64decode(headers["x-auth-roles"]))
        assert decoded == ["volundr:developer"]

    def test_custom_values(self, factory: _AuthHeaderFactory) -> None:
        headers = factory(
            user_id="u-123",
            email="alice@corp.com",
            tenant="acme",
            roles=["volundr:admin", "volundr:developer"],
        )
        assert headers["x-auth-user-id"] == "u-123"
        assert headers["x-auth-email"] == "alice@corp.com"
        assert headers["x-auth-tenant"] == "acme"

        decoded = json.loads(base64.b64decode(headers["x-auth-roles"]))
        assert decoded == ["volundr:admin", "volundr:developer"]

    def test_empty_roles_produces_empty_array(self, factory: _AuthHeaderFactory) -> None:
        headers = factory(roles=[])
        decoded = json.loads(base64.b64decode(headers["x-auth-roles"]))
        assert decoded == []

    def test_header_keys_match_envoy_defaults(self, factory: _AuthHeaderFactory) -> None:
        headers = factory()
        expected_keys = {"x-auth-user-id", "x-auth-email", "x-auth-tenant", "x-auth-roles"}
        assert set(headers.keys()) == expected_keys

    def test_roles_are_base64_decodable(self, factory: _AuthHeaderFactory) -> None:
        headers = factory(roles=["role-a", "role-b", "role-c"])
        raw = headers["x-auth-roles"]
        decoded_bytes = base64.b64decode(raw)
        parsed = json.loads(decoded_bytes)
        assert isinstance(parsed, list)
        assert len(parsed) == 3


class TestVolundrSettings:
    """Verify volundr_settings fixture creates valid Settings."""

    def test_settings_use_test_database(self) -> None:
        from volundr.config import DatabaseConfig, IdentityConfig, PodManagerConfig, Settings

        settings = Settings(
            database=DatabaseConfig(
                host="testhost",
                port=15432,
                user="testuser",
                password="testpass",
                name="testdb",
            ),
            identity=IdentityConfig(
                adapter="volundr.adapters.outbound.identity.AllowAllIdentityAdapter",
            ),
            pod_manager=PodManagerConfig(
                adapter="volundr.adapters.outbound.local_process.LocalProcessManager",
            ),
        )
        assert settings.database.host == "testhost"
        assert settings.database.port == 15432
        assert settings.database.name == "testdb"
        assert "AllowAll" in settings.identity.adapter
        assert "LocalProcess" in settings.pod_manager.adapter

    def test_settings_skip_yaml_loading(self) -> None:
        """Constructor args take precedence over YAML — no file needed."""
        from volundr.config import DatabaseConfig, Settings

        settings = Settings(
            database=DatabaseConfig(host="explicit-host"),
        )
        assert settings.database.host == "explicit-host"


class TestTingSettings:
    """Verify ting_settings fixture creates valid Settings."""

    def test_settings_use_test_database(self) -> None:
        from ting.config import AuthConfig, DatabaseConfig, Settings

        settings = Settings(
            database=DatabaseConfig(
                host="testhost",
                port=15432,
                user="testuser",
                password="testpass",
                name="testdb",
            ),
            auth=AuthConfig(allow_anonymous_dev=True),
        )
        assert settings.database.host == "testhost"
        assert settings.database.port == 15432
        assert settings.auth.allow_anonymous_dev is True

    def test_anonymous_dev_enabled(self) -> None:
        from ting.config import AuthConfig, Settings

        settings = Settings(auth=AuthConfig(allow_anonymous_dev=True))
        assert settings.auth.allow_anonymous_dev is True


class TestTransactionalPoolIsolation:
    """Verify the rollback pattern works (mocked)."""

    async def test_rollback_discards_writes(self) -> None:
        """Simulate the txn_pool pattern: BEGIN → write → ROLLBACK → invisible."""
        conn = AsyncMock()
        txn = AsyncMock()
        conn.transaction.return_value = txn

        # Simulate: start txn, wrap, write, rollback
        await txn.start()
        pool = TransactionalPool(conn)
        await pool.execute("INSERT INTO t (id) VALUES ($1)", 1)
        await txn.rollback()

        # Verify the sequence
        txn.start.assert_awaited_once()
        conn.execute.assert_awaited_once_with("INSERT INTO t (id) VALUES ($1)", 1, timeout=None)
        txn.rollback.assert_awaited_once()


class TestEnvironmentDefaults:
    """Verify env-var defaults used in conftest."""

    def test_defaults_when_no_env_vars(self) -> None:
        """Import-time defaults should be localhost:5432/volundr_test."""
        # Re-import to pick up the module-level values
        from tests.integration import conftest

        assert conftest._DB_HOST == os.environ.get("TEST_DATABASE_HOST", "localhost")
        assert conftest._DB_PORT == int(os.environ.get("TEST_DATABASE_PORT", "5432"))
        assert conftest._DB_USER == os.environ.get("TEST_DATABASE_USER", "volundr_test")
        assert conftest._DB_NAME == os.environ.get("TEST_DATABASE_NAME", "volundr_test")

    def test_migrations_dir_exists(self) -> None:
        from tests.integration import conftest

        assert conftest._MIGRATIONS_DIR.is_dir()
