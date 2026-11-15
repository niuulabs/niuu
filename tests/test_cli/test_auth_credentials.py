"""Tests for cli.auth.credentials — encrypted credential storage."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from cli.auth import credentials as module
from cli.auth.credentials import CredentialReadError, CredentialStore, StoredTokens


@pytest.fixture
def cred_path(tmp_path: Path) -> Path:
    return tmp_path / "login"


@pytest.fixture
def store(cred_path: Path) -> CredentialStore:
    return CredentialStore(path=cred_path)


@pytest.fixture(autouse=True)
def _set_credential_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NIUU_CREDENTIAL_KEY", "test-secret-key-for-ci")


class TestKey:
    def test_the_explicit_key_replaces_the_key_file(self, store, cred_path) -> None:
        store.store(StoredTokens(access_token="x"))
        assert not cred_path.with_name("login.key").exists()

    def test_a_different_explicit_key_cannot_read_the_login(
        self, store, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store.store(StoredTokens(access_token="x"))
        monkeypatch.setenv("NIUU_CREDENTIAL_KEY", "other-key")
        with pytest.raises(CredentialReadError, match="run `niuu login` again"):
            store.load()


class TestKeyFile:
    @pytest.fixture(autouse=True)
    def _no_explicit_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NIUU_CREDENTIAL_KEY", raising=False)

    def test_the_key_is_made_once_private_and_survives_a_hostname_change(
        self, store, cred_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store.store(StoredTokens(access_token="first"))
        key = cred_path.with_name("login.key")
        assert oct(key.stat().st_mode & 0o777) == "0o600"
        made = key.read_bytes()
        real = os.uname()
        renamed = SimpleNamespace(
            **{f: getattr(real, f) for f in ("sysname", "release", "version", "machine")},
            nodename="MacBook-Pro.local",
        )
        monkeypatch.setattr(module.os, "uname", lambda: renamed)
        store.store(StoredTokens(access_token="second"))
        assert key.read_bytes() == made
        assert store.load().access_token == "second"

    def test_a_login_whose_key_is_gone_says_to_log_in_again(self, store, cred_path) -> None:
        store.store(StoredTokens(access_token="x"))
        cred_path.with_name("login.key").unlink()
        with pytest.raises(CredentialReadError, match="login.key is missing"):
            store.load()


class TestEarlierLogin:
    """A login an earlier CLI kept at ~/.niuu/credentials, keyed by the hostname."""

    @pytest.fixture
    def home(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        monkeypatch.delenv("NIUU_CREDENTIAL_KEY", raising=False)
        monkeypatch.setenv("NIUU_HOME", str(tmp_path / "niuu"))
        (tmp_path / "niuu").mkdir()
        return tmp_path / "niuu"

    def test_it_moves_to_the_login_file(self, home: Path) -> None:
        legacy = home / "credentials"
        token = Fernet(module._legacy_key()).encrypt(b'{"access_token": "old", "issuer": "i"}')
        legacy.write_bytes(token)

        loaded = CredentialStore().load()

        assert (loaded.access_token, loaded.issuer) == ("old", "i")
        assert not legacy.exists()
        assert CredentialStore().load().access_token == "old"
        assert (home / "login").is_file() and (home / "login.key").is_file()

    def test_one_from_another_hostname_says_what_to_do(self, home: Path) -> None:
        (home / "credentials").write_bytes(Fernet(Fernet.generate_key()).encrypt(b"{}"))
        with pytest.raises(CredentialReadError, match="another hostname"):
            CredentialStore().load()

    def test_the_platforms_credentials_directory_is_never_touched(self, home: Path) -> None:
        platform = home / "credentials"
        (platform / "user").mkdir(parents=True)
        store = CredentialStore()
        assert store.load() is None
        store.store(StoredTokens(access_token="x"))
        store.clear()
        assert (platform / "user").is_dir()
        assert store.load() is None


class TestCredentialStoreRoundTrip:
    def test_store_and_load(self, store: CredentialStore, cred_path: Path) -> None:
        tokens = StoredTokens(
            access_token="access123",
            refresh_token="refresh456",
            id_token="id789",
            token_type="Bearer",
            expires_at=1700000000.0,
            issuer="https://idp.example.com",
        )
        store.store(tokens)
        assert cred_path.exists()

        loaded = store.load()
        assert loaded is not None
        assert loaded.access_token == "access123"
        assert loaded.refresh_token == "refresh456"
        assert loaded.id_token == "id789"
        assert loaded.expires_at == 1700000000.0
        assert loaded.issuer == "https://idp.example.com"

    def test_file_permissions(self, store: CredentialStore, cred_path: Path) -> None:
        store.store(StoredTokens(access_token="x"))
        mode = oct(cred_path.stat().st_mode & 0o777)
        assert mode == "0o600"

    def test_load_returns_none_when_missing(self, store: CredentialStore) -> None:
        assert store.load() is None

    def test_corrupt_data_is_an_error_that_says_to_log_in_again(
        self,
        store: CredentialStore,
        cred_path: Path,
    ) -> None:
        cred_path.parent.mkdir(parents=True, exist_ok=True)
        cred_path.write_bytes(b"garbage")
        with pytest.raises(CredentialReadError, match="run `niuu login` again"):
            store.load()

    def test_clear_removes_file(self, store: CredentialStore, cred_path: Path) -> None:
        store.store(StoredTokens(access_token="x"))
        assert cred_path.exists()
        store.clear()
        assert not cred_path.exists()

    def test_clear_when_no_file(self, store: CredentialStore) -> None:
        store.clear()  # should not raise

    def test_overwrite_existing(self, store: CredentialStore) -> None:
        store.store(StoredTokens(access_token="first"))
        store.store(StoredTokens(access_token="second"))
        loaded = store.load()
        assert loaded is not None
        assert loaded.access_token == "second"
