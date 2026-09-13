"""File-based CredentialStore adapter with optional Fernet encryption.

Stores credentials as JSON files on disk, one file per owner at
``{base_dir}/{owner_type}/{owner_id}/credentials.json``.

Designed for the **dynamic adapter pattern**: the constructor accepts
plain ``**kwargs`` so it can be wired from YAML config without code
changes.

Example YAML::

    credential_store:
      adapter: "volundr.adapters.outbound.file_credential_store.FileCredentialStore"
      base_dir: "~/.volundr/user-credentials"
      encryption_key: ""   # Fernet key; leave empty to disable encryption
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from volundr.domain.models import SecretType, StoredCredential
from volundr.domain.ports import CredentialStorePort

logger = logging.getLogger(__name__)


class FileCredentialStore(CredentialStorePort):
    """File-backed implementation of CredentialStorePort.

    Persists credentials as JSON files on disk.  When *encryption_key*
    is provided, file contents are encrypted with Fernet (symmetric
    authenticated encryption) before writing to disk.

    Atomic writes are ensured by writing to a temporary file first and
    then calling ``os.replace()`` to move it into place.
    """

    def __init__(
        self,
        *,
        base_dir: str = "~/.volundr/user-credentials",
        encryption_key: str = "",
        **_extra: object,
    ) -> None:
        self._base_dir = Path(base_dir).expanduser()
        self._fernet = None
        if encryption_key:
            from cryptography.fernet import Fernet

            self._fernet = Fernet(encryption_key.encode())
        self._locks: dict[Path, asyncio.Lock] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _owner_path(self, owner_type: str, owner_id: str) -> Path:
        return self._base_dir / owner_type / owner_id / "credentials.json"

    def _lock_for(self, path: Path) -> asyncio.Lock:
        if path not in self._locks:
            self._locks[path] = asyncio.Lock()
        return self._locks[path]

    def _read_file(self, path: Path) -> dict:
        """Read and optionally decrypt the credentials file."""
        if not path.exists():
            return {"metadata": {}, "values": {}}

        raw = path.read_bytes()
        if self._fernet is not None:
            raw = self._fernet.decrypt(raw)

        return json.loads(raw)

    def _write_file(self, path: Path, data: dict) -> None:
        """Atomically write (and optionally encrypt) the credentials file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, default=str).encode()

        if self._fernet is not None:
            payload = self._fernet.encrypt(payload)

        fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        closed = False
        try:
            os.write(fd, payload)
            os.close(fd)
            closed = True
            os.replace(tmp_path, path)
        except BaseException:
            if not closed:
                os.close(fd)
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def _row_to_credential(self, entry: dict) -> StoredCredential:
        return StoredCredential(
            id=entry["id"],
            name=entry["name"],
            secret_type=SecretType(entry["secret_type"]),
            keys=tuple(entry["keys"]),
            metadata=entry.get("metadata", {}),
            owner_id=entry["owner_id"],
            owner_type=entry["owner_type"],
            created_at=datetime.fromisoformat(entry["created_at"]),
            updated_at=datetime.fromisoformat(entry["updated_at"]),
        )

    def _credential_to_dict(self, cred: StoredCredential) -> dict:
        return {
            "id": cred.id,
            "name": cred.name,
            "secret_type": cred.secret_type.value,
            "keys": list(cred.keys),
            "metadata": cred.metadata,
            "owner_id": cred.owner_id,
            "owner_type": cred.owner_type,
            "created_at": cred.created_at.isoformat(),
            "updated_at": cred.updated_at.isoformat(),
        }

    def _individual_path(self, owner_type: str, owner_id: str, name: str) -> Path:
        """Path for an individual credential JSON file (for direct mounting)."""
        return self._base_dir / owner_type / owner_id / f"{name}.json"

    def _write_individual_credential(
        self,
        owner_type: str,
        owner_id: str,
        name: str,
        data: dict[str, str],
    ) -> None:
        """Write an individual credential JSON file alongside the main store.

        These files are mounted directly into pods by FileSecretInjectionAdapter
        and read by the entrypoint's manifest-based secret sourcing.
        """
        path = self._individual_path(owner_type, owner_id, name)
        payload = json.dumps(data).encode()
        if self._fernet is not None:
            payload = self._fernet.encrypt(payload)

        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        closed = False
        try:
            os.write(fd, payload)
            os.close(fd)
            closed = True
            os.replace(tmp_path, path)
        except BaseException:
            if not closed:
                os.close(fd)
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def _delete_individual_credential(
        self,
        owner_type: str,
        owner_id: str,
        name: str,
    ) -> None:
        """Remove the individual credential file if it exists."""
        path = self._individual_path(owner_type, owner_id, name)
        if path.exists():
            path.unlink()

    # ------------------------------------------------------------------
    # CredentialStorePort implementation
    # ------------------------------------------------------------------

    async def store(
        self,
        owner_type: str,
        owner_id: str,
        name: str,
        secret_type: SecretType,
        data: dict[str, str],
        metadata: dict | None = None,
    ) -> StoredCredential:
        path = self._owner_path(owner_type, owner_id)
        async with self._lock_for(path):
            file_data = self._read_file(path)
            now = datetime.now(UTC)

            existing_entry = file_data["metadata"].get(name)
            cred_id = existing_entry["id"] if existing_entry else str(uuid4())
            created_at = (
                datetime.fromisoformat(existing_entry["created_at"]) if existing_entry else now
            )

            credential = StoredCredential(
                id=cred_id,
                name=name,
                secret_type=secret_type,
                keys=tuple(data.keys()),
                metadata=metadata or {},
                owner_id=owner_id,
                owner_type=owner_type,
                created_at=created_at,
                updated_at=now,
            )

            file_data["metadata"][name] = self._credential_to_dict(credential)
            file_data["values"][name] = dict(data)
            self._write_file(path, file_data)

            # Write individual credential file for direct mounting
            self._write_individual_credential(owner_type, owner_id, name, data)

        logger.debug("Stored a credential for %r/%r", owner_type, owner_id)
        return credential

    async def get(
        self,
        owner_type: str,
        owner_id: str,
        name: str,
    ) -> StoredCredential | None:
        path = self._owner_path(owner_type, owner_id)
        file_data = self._read_file(path)
        entry = file_data["metadata"].get(name)
        if entry is None:
            return None
        return self._row_to_credential(entry)

    async def get_value(
        self,
        owner_type: str,
        owner_id: str,
        name: str,
    ) -> dict[str, str] | None:
        path = self._owner_path(owner_type, owner_id)
        file_data = self._read_file(path)
        return file_data["values"].get(name)

    async def delete(
        self,
        owner_type: str,
        owner_id: str,
        name: str,
    ) -> None:
        path = self._owner_path(owner_type, owner_id)
        async with self._lock_for(path):
            file_data = self._read_file(path)
            file_data["metadata"].pop(name, None)
            file_data["values"].pop(name, None)
            self._write_file(path, file_data)
            self._delete_individual_credential(owner_type, owner_id, name)

        logger.debug("Deleted a credential for %r/%r", owner_type, owner_id)

    async def list(
        self,
        owner_type: str,
        owner_id: str,
        secret_type: SecretType | None = None,
    ) -> list[StoredCredential]:
        path = self._owner_path(owner_type, owner_id)
        file_data = self._read_file(path)
        results = [self._row_to_credential(entry) for entry in file_data["metadata"].values()]
        if secret_type is not None:
            results = [c for c in results if c.secret_type == secret_type]
        return sorted(results, key=lambda c: c.name)

    async def health_check(self) -> bool:
        try:
            self._base_dir.mkdir(parents=True, exist_ok=True)
            test_file = self._base_dir / ".health_check"
            test_file.write_text("ok")
            test_file.unlink()
            return True
        except OSError:
            logger.warning("Health check failed for %s", self._base_dir)
            return False
