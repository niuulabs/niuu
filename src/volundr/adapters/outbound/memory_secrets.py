"""In-memory secret manager adapter.

Used as a default when no Kubernetes cluster is available,
and in tests.
"""

from __future__ import annotations

import re

from credentials.models import SecretInfo
from credentials.ports import (
    SecretAlreadyExistsError,
    SecretManager,
    SecretValidationError,
)

# RFC 1123 subdomain: lowercase alphanumeric, hyphens, max 253 chars
_K8S_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9\-]{0,251}[a-z0-9])?$")


def validate_k8s_name(name: str) -> None:
    """Validate a Kubernetes resource name."""
    if not name or not _K8S_NAME_RE.match(name):
        raise SecretValidationError(
            f"Invalid secret name: '{name}'. Must be a lowercase RFC 1123 subdomain "
            "(alphanumeric and hyphens, 1-253 characters, must start/end with alphanumeric)."
        )


class InMemorySecretManager(SecretManager):
    """In-memory implementation of SecretManager."""

    def __init__(self, secrets: list[SecretInfo] | None = None) -> None:
        self._secrets: dict[str, SecretInfo] = {}
        for s in secrets or []:
            self._secrets[s.name] = s

    async def list(self) -> list[SecretInfo]:
        """List all secrets sorted by name."""
        return sorted(self._secrets.values(), key=lambda s: s.name)

    async def get(self, name: str) -> SecretInfo | None:
        """Get secret metadata by name."""
        return self._secrets.get(name)

    async def create(self, name: str, data: dict[str, str]) -> SecretInfo:
        """Create a new secret."""
        validate_k8s_name(name)

        if name in self._secrets:
            raise SecretAlreadyExistsError(f"Secret already exists: {name}")

        info = SecretInfo(name=name, keys=list(data.keys()))
        self._secrets[name] = info
        return info
