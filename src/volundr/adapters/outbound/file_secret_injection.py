"""File-based SecretInjection adapter for local development.

Mounts user credential files from a local directory into session pods
via a hostPath volume. No CSI driver or SecretProviderClass needed.

Uses the dynamic adapter pattern (plain **kwargs constructor).
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

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
        self._base_dir = str(Path(base_dir).expanduser())

    async def pod_spec_additions(
        self,
        user_id: str,
        session_id: str,
    ) -> PodSpecAdditions:
        """Return hostPath volume mounting user credential files."""
        host_path = f"{self._base_dir}/user/{user_id}"
        volume_name = f"secrets-{session_id}"

        projected = Path(self._base_dir) / "sessions" / session_id
        projection_mounts = []
        projection_volumes = []
        if (projected / "mounts.json").exists():
            targets = json.loads((projected / "mounts.json").read_text())
            for index, target in enumerate(targets):
                name = f"secret-file-{index}"
                projection_volumes.append(
                    {
                        "name": name,
                        "hostPath": {"path": str(projected / str(index)), "type": "File"},
                    }
                )
                projection_mounts.append({"name": name, "mountPath": target, "readOnly": True})

        return PodSpecAdditions(
            volumes=(
                {
                    "name": volume_name,
                    "hostPath": {
                        "path": host_path,
                        "type": "DirectoryOrCreate",
                    },
                },
                *projection_volumes,
            ),
            volume_mounts=(
                {
                    "name": volume_name,
                    "mountPath": "/run/secrets/user",
                    "readOnly": True,
                },
                *projection_mounts,
            ),
        )

    async def ensure_secret_provider_class(
        self,
        user_id: str,
        credential_mappings: list[CredentialMapping],
        session_id: str | None = None,
        tenant_id: str | None = None,
    ) -> None:
        """Materialize requested fields for init containers and runtime Git helpers."""
        if not session_id:
            return
        projected = Path(self._base_dir) / "sessions" / session_id
        targets: list[str] = []
        for mapping in credential_mappings:
            if not mapping.file_mappings:
                continue
            source = Path(self._base_dir) / "user" / user_id / mapping.credential_name
            data = json.loads(source.read_text())
            projected.mkdir(parents=True, exist_ok=True, mode=0o700)
            for target, key in mapping.file_mappings.items():
                if key not in data:
                    raise ValueError(
                        f"Credential {mapping.credential_name!r} is missing field {key!r}"
                    )
                dest = projected / str(len(targets))
                # The parent is private on the host; the bind-mounted file must be
                # readable by the pod UID, which can differ from the host UID.
                dest.touch(mode=0o644)
                dest.chmod(0o644)
                dest.write_text(data[key])
                targets.append(target)
        if targets:
            (projected / "mounts.json").write_text(json.dumps(targets))

    async def cleanup_session(self, session_id: str) -> None:
        projected = Path(self._base_dir) / "sessions" / session_id
        if projected.exists():
            shutil.rmtree(projected)

    async def provision_user(self, user_id: str) -> None:
        """No-op — directory creation is handled by DirectoryOrCreate."""

    async def deprovision_user(self, user_id: str) -> None:
        """No-op — local credential files are retained."""
