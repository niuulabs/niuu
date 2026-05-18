"""File-based SecretInjection adapter for local development.

Mounts user credential files from a local directory into session pods
via a hostPath volume. No CSI driver or SecretProviderClass needed.

Uses the dynamic adapter pattern (plain **kwargs constructor).
"""

from __future__ import annotations

import logging

from volundr.domain.models import CredentialMapping, PodSpecAdditions
from volundr.domain.ports import SecretInjectionPort

logger = logging.getLogger(__name__)


class FileSecretInjectionAdapter(SecretInjectionPort):
    """File-based secret injection for local dev environments.

    Mounts ``{base_dir}/user/{user_id}/`` into the pod at
    ``/run/secrets/user`` via hostPath so the entrypoint can
    read credential JSON files.

    Args:
        base_dir: Root directory for credential files.
    """

    def __init__(
        self,
        *,
        base_dir: str = "~/.volundr/user-credentials",
        **_extra: object,
    ) -> None:
        from pathlib import Path

        self._base_dir = str(Path(base_dir).expanduser())

    async def pod_spec_additions(
        self,
        user_id: str,
        session_id: str,
    ) -> PodSpecAdditions:
        """Return hostPath volume mounting user credential files."""
        host_path = f"{self._base_dir}/user/{user_id}"
        volume_name = f"secrets-{session_id}"

        return PodSpecAdditions(
            volumes=(
                {
                    "name": volume_name,
                    "hostPath": {
                        "path": host_path,
                        "type": "DirectoryOrCreate",
                    },
                },
            ),
            volume_mounts=(
                {
                    "name": volume_name,
                    "mountPath": "/run/secrets/user",
                    "readOnly": True,
                },
            ),
        )

    async def ensure_secret_provider_class(
        self,
        user_id: str,
        credential_mappings: list[CredentialMapping],
        session_id: str | None = None,
        tenant_id: str | None = None,
    ) -> None:
        """No-op for file-based adapter."""

    async def provision_user(self, user_id: str) -> None:
        """No-op — directory creation is handled by DirectoryOrCreate."""

    async def deprovision_user(self, user_id: str) -> None:
        """No-op — local credential files are retained."""
