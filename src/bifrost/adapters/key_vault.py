"""Key vault adapter implementations for Bifröst.

Two adapters are provided:

- ``EnvKeyVault``         — reads API keys from environment variables.
- ``SecretsFileKeyVault`` — reads API keys from a YAML secrets file.

Both adapters cache keys in memory and support live rotation via ``reload()``.
Keys are *never* written to logs or returned in any HTTP response.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from bifrost.config import BifrostConfig
from bifrost.ports.key_vault import KeyVaultPort

logger = logging.getLogger(__name__)


class EnvKeyVault(KeyVaultPort):
    """Load provider API keys from environment variables at startup.

    Keys are resolved from the ``api_key_env`` field of each provider's
    config, then cached in memory.  Call ``reload()`` to pick up rotated
    values (e.g. after a secret manager rotation + SIGHUP) without
    restarting the process.

    If a provider also sets ``api_key_file``, the file is tried as a
    fallback when the env var is absent or empty.

    Keys are never written to logs — only provider names are logged.
    """

    def __init__(self, config: BifrostConfig) -> None:
        self._config = config
        self._keys: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        """Populate the in-memory key cache from env vars and secret files."""
        loaded: list[str] = []
        for provider_name, provider_cfg in self._config.providers.items():
            key = ""

            # Prefer env var.
            if provider_cfg.api_key_env:
                key = os.environ.get(provider_cfg.api_key_env, "")

            # Fall back to secrets file entry.
            if not key and provider_cfg.api_key_file:
                key = _read_secret_file(provider_cfg.api_key_file)

            if key:
                self._keys[provider_name] = key
                loaded.append(provider_name)

        logger.info("KeyVault: loaded keys for providers: %s", loaded)

    def get_key(self, provider: str) -> str | None:
        """Return the cached API key for *provider*, or ``None`` if absent."""
        return self._keys.get(provider) or None

    def reload(self) -> None:
        """Re-read all provider keys from env vars / secret files."""
        self._keys.clear()
        self._load()
        logger.info("KeyVault: keys reloaded")


class SecretsFileKeyVault(KeyVaultPort):
    """Load provider API keys from a YAML secrets file.

    The file must map provider names to their API keys::

        anthropic: sk-ant-...
        openai: sk-...

    This adapter is suitable for deployments where secrets are injected
    as mounted files (e.g. Kubernetes ``secretKeyRef`` projected volumes).
    Call ``reload()`` after the file is updated to pick up new values.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._keys: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        """Parse the secrets file and populate the in-memory cache."""
        loaded: list[str] = []
        raw = _read_secret_file(self._path)
        if not raw:
            logger.warning("KeyVault: secrets file is empty or missing")
            return

        try:
            data: dict = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            logger.error("KeyVault: failed to parse secrets file: %s", type(exc).__name__)
            return

        if not isinstance(data, dict):
            logger.error("KeyVault: secrets file must be a YAML mapping")
            return

        for provider, key in data.items():
            if isinstance(key, str) and key:
                self._keys[provider] = key
                loaded.append(provider)

        logger.info("KeyVault: loaded keys for providers: %s", loaded)

    def get_key(self, provider: str) -> str | None:
        """Return the cached API key for *provider*, or ``None`` if absent."""
        return self._keys.get(provider) or None

    def reload(self) -> None:
        """Re-parse the secrets file and refresh the in-memory cache."""
        self._keys.clear()
        self._load()
        logger.info("KeyVault: secrets file keys reloaded")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_secret_file(path: str) -> str:
    """Read and return the stripped content of *path*, or ``""`` on error."""
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""
