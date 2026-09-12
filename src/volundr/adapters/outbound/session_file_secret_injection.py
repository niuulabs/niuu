"""Per-session file materialization of credentials for container sessions.

Single-host container runtimes (``niuu up`` docker mode) have no CSI driver
and no agent injector. This adapter reads the credentials that
``FileCredentialStore`` persists, renders exactly the fields a session asked
for into a private per-session directory, and hands the pod manager
hostPath volumes that bind those files into the sandbox:

* env mappings become ``/run/secrets/env.sh`` (``export NAME='value'``
  lines) which the skuld entrypoint sources on start-up;
* file mappings become one file each, bound at the requested target path.

Secret values never enter the container's ``docker inspect`` output and the
platform environment is never inherited by the sandbox.

Uses the dynamic adapter pattern (plain ``**kwargs`` constructor).
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from volundr.domain.models import CredentialMapping, PodSpecAdditions
from volundr.domain.ports import SecretInjectionPort

logger = logging.getLogger(__name__)

DEFAULT_ENV_MOUNT_PATH = "/run/secrets/env.sh"
_STORE_FILE = "credentials.json"
_ENV_FILE = "env.sh"
_MOUNTS_FILE = "mounts.json"
_FILES_DIR = "files"
# The session directory is private to the platform user; the files inside are
# bound one by one into the sandbox, whose UID can differ from the host UID,
# so they must be world-readable (the bind bypasses the parent's 0700).
_SESSION_DIR_MODE = 0o700
_MATERIALIZED_FILE_MODE = 0o644


def shell_export_line(name: str, value: str) -> str:
    """Render ``export NAME='value'`` with POSIX single-quote escaping."""
    escaped = value.replace("'", "'\\''")
    return f"export {name}='{escaped}'"


class SessionFileSecretInjectionAdapter(SecretInjectionPort):
    """Materialize requested credential fields per session for bind mounting.

    Args:
        base_dir: ``FileCredentialStore`` base directory
            (``{base_dir}/{owner_type}/{owner_id}/credentials.json``).
        encryption_key: Fernet key the store encrypts with; empty for plaintext.
        sessions_dir: Where per-session material is rendered. Defaults to
            ``{base_dir}/sessions``.
        env_mount_path: In-container path of the rendered env file.
        owner_type: Credential owner type the store was written with.
    """

    def __init__(
        self,
        *,
        base_dir: str = "~/.volundr/user-credentials",
        encryption_key: str = "",
        sessions_dir: str = "",
        env_mount_path: str = DEFAULT_ENV_MOUNT_PATH,
        owner_type: str = "user",
        **_extra: object,
    ) -> None:
        self._base_dir = Path(base_dir).expanduser()
        self._sessions_dir = (
            Path(sessions_dir).expanduser() if sessions_dir else self._base_dir / "sessions"
        )
        self._env_mount_path = env_mount_path
        self._owner_type = owner_type
        self._fernet = None
        if encryption_key:
            from cryptography.fernet import Fernet

            self._fernet = Fernet(encryption_key.encode())

    # ------------------------------------------------------------------
    # Store access
    # ------------------------------------------------------------------

    def _session_dir(self, session_id: str) -> Path:
        return self._sessions_dir / session_id

    def _read_values(self, owner_id: str) -> dict[str, dict[str, str]]:
        path = self._base_dir / self._owner_type / owner_id / _STORE_FILE
        if not path.exists():
            return {}
        raw = path.read_bytes()
        if self._fernet is not None:
            raw = self._fernet.decrypt(raw)
        data = json.loads(raw)
        values = data.get("values", {})
        return values if isinstance(values, dict) else {}

    @staticmethod
    def _field(values: dict[str, dict[str, str]], credential: str, key: str) -> str:
        stored = values.get(credential)
        if stored is None:
            raise ValueError(
                f"Credential {credential!r} is not in the credential store; "
                "re-create the integration connection or detach it from the session"
            )
        if key not in stored:
            raise ValueError(f"Credential {credential!r} is missing field {key!r}")
        return str(stored[key])

    @staticmethod
    def _write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(mode=_MATERIALIZED_FILE_MODE)
        path.chmod(_MATERIALIZED_FILE_MODE)
        path.write_text(text)

    # ------------------------------------------------------------------
    # SecretInjectionPort
    # ------------------------------------------------------------------

    async def ensure_secret_provider_class(
        self,
        user_id: str,
        credential_mappings: list[CredentialMapping],
        session_id: str | None = None,
        tenant_id: str | None = None,
    ) -> None:
        """Render the requested fields for one session."""
        if not session_id:
            return
        session_dir = self._session_dir(session_id)
        if session_dir.exists():
            shutil.rmtree(session_dir)
        session_dir.mkdir(parents=True)
        session_dir.chmod(_SESSION_DIR_MODE)
        values = self._read_values(user_id)

        env_lines: list[str] = []
        targets: list[str] = []
        for mapping in credential_mappings:
            for env_name, key in mapping.env_mappings.items():
                env_lines.append(
                    shell_export_line(env_name, self._field(values, mapping.credential_name, key))
                )
            for target, key in mapping.file_mappings.items():
                self._write(
                    session_dir / _FILES_DIR / str(len(targets)),
                    self._field(values, mapping.credential_name, key),
                )
                targets.append(target)

        if env_lines:
            self._write(session_dir / _ENV_FILE, "\n".join(env_lines) + "\n")
        if targets:
            self._write(session_dir / _MOUNTS_FILE, json.dumps(targets))
        logger.info(
            "Materialized %d env and %d file credential(s) for session %s",
            len(env_lines),
            len(targets),
            session_id,
        )

    async def pod_spec_additions(
        self,
        user_id: str,
        session_id: str,
    ) -> PodSpecAdditions:
        """hostPath volumes for everything rendered by ``ensure_secret_provider_class``."""
        del user_id
        session_dir = self._session_dir(session_id)
        volumes: list[dict] = []
        mounts: list[dict] = []

        env_file = session_dir / _ENV_FILE
        if env_file.exists():
            volumes.append(
                {"name": "secret-env", "hostPath": {"path": str(env_file), "type": "File"}}
            )
            mounts.append(
                {"name": "secret-env", "mountPath": self._env_mount_path, "readOnly": True}
            )

        mounts_file = session_dir / _MOUNTS_FILE
        if mounts_file.exists():
            for index, target in enumerate(json.loads(mounts_file.read_text())):
                name = f"secret-file-{index}"
                volumes.append(
                    {
                        "name": name,
                        "hostPath": {
                            "path": str(session_dir / _FILES_DIR / str(index)),
                            "type": "File",
                        },
                    }
                )
                mounts.append({"name": name, "mountPath": target, "readOnly": True})

        return PodSpecAdditions(volumes=tuple(volumes), volume_mounts=tuple(mounts))

    async def cleanup_session(self, session_id: str) -> None:
        session_dir = self._session_dir(session_id)
        if session_dir.exists():
            shutil.rmtree(session_dir)

    async def provision_user(self, user_id: str) -> None:
        """No-op — the credential store owns the per-user directory."""

    async def deprovision_user(self, user_id: str) -> None:
        """No-op — stored credentials are retained."""
