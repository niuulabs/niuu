"""Tests for resolve_secret_kwargs in niuu.utils."""

import os
from unittest.mock import patch

from niuu.utils import resolve_secret_kwargs


class TestResolveSecretKwargs:
    """Tests for the _resolve_secret_kwargs helper function."""

    def test_empty_secret_kwargs_env_returns_kwargs_unchanged(self) -> None:
        kwargs = {"url": "http://example.com", "timeout": 30}
        result = resolve_secret_kwargs(kwargs, {})
        assert result == kwargs
        # Should return same object when nothing to resolve
        assert result is kwargs

    def test_merges_env_var_values_into_kwargs(self) -> None:
        kwargs = {"url": "http://example.com"}
        secret_kwargs_env = {"api_key": "MY_API_KEY_ENV"}

        with patch.dict(os.environ, {"MY_API_KEY_ENV": "secret-value-123"}):
            result = resolve_secret_kwargs(kwargs, secret_kwargs_env)

        assert result == {"url": "http://example.com", "api_key": "secret-value-123"}

    def test_env_var_overrides_existing_kwarg(self) -> None:
        kwargs = {"url": "http://example.com", "token": "plaintext-token"}
        secret_kwargs_env = {"token": "TOKEN_FROM_SECRET"}

        with patch.dict(os.environ, {"TOKEN_FROM_SECRET": "encrypted-token"}):
            result = resolve_secret_kwargs(kwargs, secret_kwargs_env)

        assert result["token"] == "encrypted-token"
        assert result["url"] == "http://example.com"

    def test_missing_env_var_does_not_add_kwarg(self) -> None:
        kwargs = {"url": "http://example.com"}
        secret_kwargs_env = {"missing_key": "NONEXISTENT_ENV_VAR"}

        with patch.dict(os.environ, {}, clear=False):
            # Ensure the env var doesn't exist
            os.environ.pop("NONEXISTENT_ENV_VAR", None)
            result = resolve_secret_kwargs(kwargs, secret_kwargs_env)

        assert result == {"url": "http://example.com"}
        assert "missing_key" not in result

    def test_multiple_secret_kwargs(self) -> None:
        kwargs = {"url": "http://vault.example.com"}
        secret_kwargs_env = {
            "client_id": "VAULT_CLIENT_ID",
            "client_secret": "VAULT_CLIENT_SECRET",
            "api_token": "VAULT_TOKEN",
        }

        env = {
            "VAULT_CLIENT_ID": "my-client",
            "VAULT_CLIENT_SECRET": "super-secret",
            "VAULT_TOKEN": "tok-123",
        }
        with patch.dict(os.environ, env):
            result = resolve_secret_kwargs(kwargs, secret_kwargs_env)

        assert result == {
            "url": "http://vault.example.com",
            "client_id": "my-client",
            "client_secret": "super-secret",
            "api_token": "tok-123",
        }

    def test_does_not_mutate_original_kwargs(self) -> None:
        kwargs = {"url": "http://example.com"}
        secret_kwargs_env = {"token": "MY_TOKEN"}

        with patch.dict(os.environ, {"MY_TOKEN": "secret"}):
            result = resolve_secret_kwargs(kwargs, secret_kwargs_env)

        assert "token" not in kwargs
        assert "token" in result

    def test_partial_env_vars_set(self) -> None:
        """Only env vars that are set should be merged."""
        kwargs = {"url": "http://example.com"}
        secret_kwargs_env = {
            "present_key": "PRESENT_ENV",
            "absent_key": "ABSENT_ENV",
        }

        with patch.dict(os.environ, {"PRESENT_ENV": "found"}, clear=False):
            os.environ.pop("ABSENT_ENV", None)
            result = resolve_secret_kwargs(kwargs, secret_kwargs_env)

        assert result == {"url": "http://example.com", "present_key": "found"}
        assert "absent_key" not in result
