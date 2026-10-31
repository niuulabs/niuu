"""Persistent workspace claims for OpenShell residents, and the copy onto them.

An OpenShell resident's ``/sandbox`` lives as long as its sandbox, and a sandbox
is recreated whenever its image changes. Völundr therefore gives each resident
its own PersistentVolumeClaim (``resident-<id>-workspace``) in the namespace where
the OpenShell gateway runs sandboxes, mounted at the sandbox workspace. The
gateway cannot create volumes, so Völundr creates and deletes the claim through
Kubernetes, the way the Flux resident controller does (#1112).

A resident whose sandbox predates its claim is moved onto it once: its state is
archived out through the sandbox exec channel, restored into the claim mounted
in a new sandbox, and verified before the old sandbox is removed. The claim's
annotations record that migration's progress and which Völundr holds it, so a
restart or a second replica resumes or waits instead of starting over blindly.
"""

from __future__ import annotations

import hashlib
import tarfile
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import IO, Any

from volundr.adapters.outbound.flux import (
    LABEL_RESIDENT_ID,
    LABEL_RESIDENT_OWNER_ID,
    LABEL_RESIDENT_TENANT_ID,
)
from volundr.adapters.outbound.k8s_storage_adapter import _storage_bytes, build_pvc_manifest
from volundr.domain.models import (
    LABEL_MANAGED_BY,
    LABEL_PVC_TYPE,
    ResidentDeploymentProfile,
    ResidentRuntime,
)

MANAGED_BY = "volundr"
#: Not "workspace": that is the session workspaces K8sStorageAdapter lists and manages.
RESIDENT_WORKSPACE_PVC_TYPE = "resident-workspace"
WORKSPACE_VOLUME_SETTINGS = frozenset({"storageClass", "size", "accessMode"})
#: The keys a profile may set on ``persistence`` while Völundr manages the claim.
MANAGED_PERSISTENCE_KEYS = frozenset({"enabled", "emptyDir"})

#: How far a migration got: ``copying`` until the copy is verified on the claim,
#: then ``complete``. The old sandbox (the annotated source) goes only once the
#: resident runs healthy on the claim. ``verified`` is what Völundr before that
#: order wrote between the copy and removing the old sandbox.
ANNOTATION_MIGRATION_PHASE = "volundr.niuu.io/workspace-migration"
#: The sandbox the workspace is copied from.
ANNOTATION_MIGRATION_SOURCE = "volundr.niuu.io/workspace-migration-source"
#: The Völundr that holds the migration, and until when (epoch seconds).
ANNOTATION_MIGRATION_HOLDER = "volundr.niuu.io/workspace-migration-holder"
ANNOTATION_MIGRATION_EXPIRES = "volundr.niuu.io/workspace-migration-expires-at"
#: What the verified copy holds: "files=<n> bytes=<n> sha256=<manifest digest>".
ANNOTATION_MIGRATION_DIGEST = "volundr.niuu.io/workspace-migration-digest"

PHASE_COPYING = "copying"
PHASE_VERIFIED = "verified"
PHASE_COMPLETE = "complete"
#: Phases in which the claim holds no state of its own yet: a copy may (re)start.
RESTARTABLE_PHASES = frozenset({PHASE_COPYING, PHASE_VERIFIED})

_HTTP_NOT_FOUND = 404
_HTTP_CONFLICT = 409
_HASH_CHUNK_BYTES = 1024 * 1024


class WorkspaceMigrationError(RuntimeError):
    """A workspace migration that stopped before it changed anything it can't undo."""

    reason = "MigrationFailed"


class WorkspaceTooLargeError(WorkspaceMigrationError):
    reason = "WorkspaceTooLarge"


class WorkspaceVerificationError(WorkspaceMigrationError):
    reason = "VerificationFailed"


class WorkspaceClaimInUseError(WorkspaceMigrationError):
    """The claim already holds a workspace; a copy would overwrite it."""

    reason = "ClaimInUse"


class WorkspaceMigrationHeldError(WorkspaceMigrationError):
    """Another Völundr holds this resident's migration."""

    reason = "MigrationHeld"


class WorkspaceMigrationLeaseLostError(WorkspaceMigrationError):
    reason = "MigrationLeaseLost"


@dataclass(frozen=True)
class ResidentWorkspaceClaim:
    """The claim Völundr creates and deletes for one OpenShell resident."""

    name: str
    size: str
    storage_class: str
    access_mode: str
    mount_path: str

    @property
    def size_bytes(self) -> int:
        return int(_storage_bytes(self.size))


def resident_workspace_claim_name(runtime: ResidentRuntime) -> str:
    return f"resident-{runtime.id}-workspace"


def resident_workspace_claim(
    runtime: ResidentRuntime,
    profile: ResidentDeploymentProfile,
    *,
    size: str,
    storage_class: str,
    access_mode: str,
    mount_path: str,
) -> ResidentWorkspaceClaim | None:
    """The claim this resident owns, or None when its profile chose its own storage.

    A profile without ``persistence`` gets a Völundr-managed claim. One that names
    its own ``persistence.existingClaim`` keeps it; ``persistence.enabled: false``
    or ``persistence.emptyDir`` keeps the workspace in the sandbox only. Size,
    storage class and access mode default to Völundr's settings and are set per
    profile in ``deployment.volumes.workspace``, as for Flux residents.
    """
    values = profile.deployment.get("values")
    values = {} if values is None else values
    if not isinstance(values, dict):
        raise ValueError(f"Resident profile {profile.id} deployment.values must be an object")
    persistence = values.get("persistence")
    if persistence is not None and not isinstance(persistence, dict):
        raise ValueError(
            f"Resident profile {profile.id} deployment.values.persistence must be an object"
        )
    if isinstance(persistence, dict):
        if persistence.get("existingClaim"):
            return None
        if persistence.get("enabled") is False or persistence.get("emptyDir"):
            return None
        unknown = set(persistence) - MANAGED_PERSISTENCE_KEYS
        if unknown:
            raise ValueError(
                f"Resident profile {profile.id} deployment.values.persistence sets "
                f"{sorted(unknown)} without an existingClaim. Völundr mounts the resident's "
                f"own claim at {mount_path}; set persistence.existingClaim to mount another."
            )
    volumes = profile.deployment.get("volumes")
    volumes = {} if volumes is None else volumes
    if not isinstance(volumes, dict):
        raise ValueError(f"Resident profile {profile.id} deployment.volumes must be an object")
    unknown_volumes = set(volumes) - {"workspace"}
    if unknown_volumes:
        raise ValueError(
            f"Resident profile {profile.id} deployment.volumes has {sorted(unknown_volumes)}; "
            "OpenShell residents have only a workspace volume"
        )
    configured = volumes.get("workspace")
    configured = {} if configured is None else configured
    if not isinstance(configured, dict):
        raise ValueError(
            f"Resident profile {profile.id} deployment.volumes.workspace must be an object"
        )
    unknown_settings = set(configured) - WORKSPACE_VOLUME_SETTINGS
    if unknown_settings:
        raise ValueError(
            f"Resident profile {profile.id} deployment.volumes.workspace has unknown settings "
            f"{sorted(unknown_settings)}; expected {sorted(WORKSPACE_VOLUME_SETTINGS)}"
        )
    settings = {"size": size, "storageClass": storage_class, "accessMode": access_mode}
    settings.update(configured)
    for setting in ("size", "accessMode"):
        if not str(settings[setting] or ""):
            raise ValueError(
                f"Resident profile {profile.id} deployment.volumes.workspace.{setting} "
                "must not be empty"
            )
    return ResidentWorkspaceClaim(
        name=resident_workspace_claim_name(runtime),
        size=str(settings["size"]),
        storage_class=str(settings["storageClass"] or ""),
        access_mode=str(settings["accessMode"]),
        mount_path=mount_path,
    )


@dataclass(frozen=True)
class WorkspaceMigrationState:
    phase: str
    source: str
    holder: str
    expires_at: float


def workspace_migration_state(pvc: Any) -> WorkspaceMigrationState | None:
    """The migration a claim records, or None for a claim no migration created."""
    annotations = pvc.metadata.annotations or {}
    phase = annotations.get(ANNOTATION_MIGRATION_PHASE)
    if not phase:
        return None
    try:
        expires_at = float(annotations.get(ANNOTATION_MIGRATION_EXPIRES) or 0)
    except ValueError:
        expires_at = 0.0
    return WorkspaceMigrationState(
        phase=str(phase),
        source=str(annotations.get(ANNOTATION_MIGRATION_SOURCE) or ""),
        holder=str(annotations.get(ANNOTATION_MIGRATION_HOLDER) or ""),
        expires_at=expires_at,
    )


class ResidentWorkspaceClaims:
    """Create, read, annotate and delete residents' workspace claims in one namespace."""

    def __init__(
        self,
        api: Callable[[], Awaitable[Any]],
        namespace: str,
        *,
        attach_labels: Mapping[str, str] | None = None,
    ) -> None:
        """``attach_labels`` approve the claim for OpenShell's resource admission."""
        if not namespace:
            raise ValueError("Resident workspace claims need the OpenShell sandbox namespace")
        self._api = api
        self.namespace = namespace
        self._attach_labels = dict(attach_labels or {})

    async def read(self, name: str) -> Any | None:
        api = await self._api()
        try:
            return await api.read_namespaced_persistent_volume_claim(
                name=name, namespace=self.namespace
            )
        except Exception as exc:
            if getattr(exc, "status", None) == _HTTP_NOT_FOUND:
                return None
            raise

    async def ensure(
        self,
        runtime: ResidentRuntime,
        claim: ResidentWorkspaceClaim,
        *,
        annotations: dict[str, str] | None = None,
    ) -> tuple[Any, bool]:
        """The resident's claim, created when missing; True when this call created it.

        An existing claim must carry this resident's labels. One smaller than the
        configured size is expanded; a claim is never shrunk. One without the
        OpenShell attach labels (created before the gateway enforced resource
        admission) gets them, or the gateway would refuse to mount it.
        """
        api = await self._api()
        manifest = build_pvc_manifest(
            name=claim.name,
            namespace=self.namespace,
            storage=claim.size,
            storage_class=claim.storage_class or None,
            access_mode=claim.access_mode,
            labels={
                LABEL_MANAGED_BY: MANAGED_BY,
                LABEL_PVC_TYPE: RESIDENT_WORKSPACE_PVC_TYPE,
                LABEL_RESIDENT_ID: str(runtime.id),
                LABEL_RESIDENT_OWNER_ID: runtime.owner_id,
                LABEL_RESIDENT_TENANT_ID: runtime.tenant_id,
                **self._attach_labels,
            },
        )
        if annotations:
            manifest.metadata.annotations = dict(annotations)
        try:
            created = await api.create_namespaced_persistent_volume_claim(
                namespace=self.namespace, body=manifest
            )
            return created, True
        except Exception as exc:
            if getattr(exc, "status", None) != _HTTP_CONFLICT:
                raise
        existing = await api.read_namespaced_persistent_volume_claim(
            name=claim.name, namespace=self.namespace
        )
        if not self.owned(existing, runtime):
            raise ValueError(
                f"PVC {self.namespace}/{claim.name} exists but is not resident "
                f"{runtime.id}'s own workspace; delete or rename it before deploying"
            )
        requested = str(existing.spec.resources.requests["storage"])
        current_labels = existing.metadata.labels or {}
        missing_labels = {
            key: value
            for key, value in self._attach_labels.items()
            if current_labels.get(key) != value
        }
        grow = _storage_bytes(requested) < claim.size_bytes
        if grow or missing_labels:
            metadata: dict[str, Any] = {"resourceVersion": existing.metadata.resource_version}
            if missing_labels:
                metadata["labels"] = missing_labels
            body: dict[str, Any] = {"metadata": metadata}
            if grow:
                body["spec"] = {"resources": {"requests": {"storage": claim.size}}}
            existing = await api.patch_namespaced_persistent_volume_claim(
                name=claim.name, namespace=self.namespace, body=body
            )
        return existing, False

    @staticmethod
    def owned(pvc: Any, runtime: ResidentRuntime) -> bool:
        labels = pvc.metadata.labels or {}
        return (
            labels.get(LABEL_MANAGED_BY) == MANAGED_BY
            and labels.get(LABEL_PVC_TYPE) == RESIDENT_WORKSPACE_PVC_TYPE
            and labels.get(LABEL_RESIDENT_ID) == str(runtime.id)
        )

    async def annotate(self, pvc: Any, annotations: dict[str, str | None]) -> Any:
        """Set (None: remove) annotations, only if the claim is unchanged since read.

        Raises the API's conflict when another writer changed the claim first.
        """
        api = await self._api()
        return await api.patch_namespaced_persistent_volume_claim(
            name=pvc.metadata.name,
            namespace=self.namespace,
            body={
                "metadata": {
                    "resourceVersion": pvc.metadata.resource_version,
                    "annotations": annotations,
                }
            },
        )

    async def delete(self, runtime: ResidentRuntime) -> bool:
        """Delete the resident's claim if it is its own; False when there is none."""
        name = resident_workspace_claim_name(runtime)
        existing = await self.read(name)
        if existing is None or not self.owned(existing, runtime):
            return False
        api = await self._api()
        try:
            await api.delete_namespaced_persistent_volume_claim(name=name, namespace=self.namespace)
        except Exception as exc:
            if getattr(exc, "status", None) != _HTTP_NOT_FOUND:
                raise
            return False
        return True


@dataclass
class WorkspaceMigrationLease:
    """One Völundr's hold on a resident's migration, kept on the claim's annotations.

    Every change is a compare-and-swap on the claim's resourceVersion, so two
    Völundr replicas never both believe they hold it. A holder that stops renewing
    (it crashed) loses the lease once it expires; the same holder (the same pod)
    takes it over at once.
    """

    claims: ResidentWorkspaceClaims
    pvc: Any
    holder: str
    lease_seconds: float
    clock: Callable[[], float] = time.time
    _lost: str = field(default="", init=False)

    @property
    def lost(self) -> str:
        return self._lost

    def _expiry(self) -> str:
        return f"{self.clock() + self.lease_seconds:.0f}"

    async def acquire(
        self, source: str, *, phase: str = PHASE_COPYING, handover: bool = False
    ) -> None:
        """Hold the migration from ``source``; a live holder elsewhere keeps it.

        ``handover`` also holds a ``complete`` claim copied from ``source``, while
        ``source`` still runs the resident: the copy is done, the move is not.
        """
        state = workspace_migration_state(self.pvc)
        handing_over = (
            handover
            and state is not None
            and state.phase == PHASE_COMPLETE
            and state.source == source
        )
        if state is not None and state.phase not in RESTARTABLE_PHASES and not handing_over:
            raise WorkspaceClaimInUseError(
                f"Volume {self.claims.namespace}/{self.pvc.metadata.name} already holds a "
                f"migrated workspace ({ANNOTATION_MIGRATION_PHASE}={state.phase})"
            )
        await self._update(
            lambda: {
                ANNOTATION_MIGRATION_PHASE: phase,
                ANNOTATION_MIGRATION_SOURCE: source,
                ANNOTATION_MIGRATION_HOLDER: self.holder,
                ANNOTATION_MIGRATION_EXPIRES: self._expiry(),
            },
            acquiring=True,
        )

    async def renew(self) -> None:
        await self._update(lambda: {ANNOTATION_MIGRATION_EXPIRES: self._expiry()})

    async def set_phase(self, phase: str, **annotations: str) -> None:
        await self._update(
            lambda: {
                ANNOTATION_MIGRATION_PHASE: phase,
                ANNOTATION_MIGRATION_EXPIRES: self._expiry(),
                **annotations,
            }
        )

    async def release(self) -> None:
        """Give the migration up; its phase stays for whoever resumes it."""
        await self._update(
            lambda: {
                ANNOTATION_MIGRATION_HOLDER: None,
                ANNOTATION_MIGRATION_EXPIRES: None,
            }
        )

    def mark_lost(self, reason: str) -> None:
        self._lost = reason

    async def _update(
        self,
        annotations: Callable[[], dict[str, str | None]],
        *,
        acquiring: bool = False,
    ) -> None:
        if self._lost:
            raise WorkspaceMigrationLeaseLostError(self._lost)
        while True:
            state = workspace_migration_state(self.pvc)
            holder = state.holder if state is not None else ""
            expired = state is None or state.expires_at <= self.clock()
            if holder and holder != self.holder and not expired:
                message = (
                    f"Völundr {holder} holds the workspace migration of "
                    f"{self.claims.namespace}/{self.pvc.metadata.name} until "
                    f"{state.expires_at:.0f}"
                )
                if not acquiring:
                    self._lost = message
                    raise WorkspaceMigrationLeaseLostError(message)
                raise WorkspaceMigrationHeldError(message)
            if not acquiring and holder != self.holder:
                self._lost = (
                    f"The workspace migration of {self.claims.namespace}/"
                    f"{self.pvc.metadata.name} is no longer held by {self.holder}"
                )
                raise WorkspaceMigrationLeaseLostError(self._lost)
            try:
                self.pvc = await self.claims.annotate(self.pvc, annotations())
                return
            except Exception as exc:
                status = getattr(exc, "status", None)
                if status == _HTTP_NOT_FOUND:
                    self._lost = f"Volume {self.claims.namespace}/{self.pvc.metadata.name} is gone"
                    raise WorkspaceMigrationLeaseLostError(self._lost) from exc
                if status != _HTTP_CONFLICT:
                    raise
            # Someone (kubelet, the PV controller, another replica) changed the claim
            # since it was read: read it again and re-check who holds the migration.
            current = await self.claims.read(self.pvc.metadata.name)
            if current is None:
                self._lost = f"Volume {self.claims.namespace}/{self.pvc.metadata.name} is gone"
                raise WorkspaceMigrationLeaseLostError(self._lost)
            self.pvc = current


@dataclass(frozen=True)
class WorkspaceManifest:
    """What a tar archive holds: path -> (kind, size, sha256 or link target)."""

    entries: dict[str, tuple[str, int, str]]

    @property
    def files(self) -> int:
        return sum(1 for kind, _, _ in self.entries.values() if kind == "file")

    @property
    def size(self) -> int:
        return sum(size for kind, size, _ in self.entries.values() if kind == "file")

    @property
    def digest(self) -> str:
        digest = hashlib.sha256()
        for path in sorted(self.entries):
            kind, size, detail = self.entries[path]
            digest.update(f"{path}\0{kind}\0{size}\0{detail}\n".encode())
        return digest.hexdigest()

    def summary(self) -> str:
        return f"files={self.files} bytes={self.size} sha256={self.digest}"

    def under(self, prefix: str) -> WorkspaceManifest:
        """The same entries, as they appear restored into ``prefix`` (a relative dir)."""
        if not prefix:
            return self
        entries = {prefix: ("dir", 0, "")}
        entries.update({f"{prefix}/{path}": entry for path, entry in self.entries.items()})
        return WorkspaceManifest(entries)

    def merged(self, other: WorkspaceManifest) -> WorkspaceManifest:
        overlap = set(self.entries) & set(other.entries)
        if overlap:
            raise WorkspaceMigrationError(
                f"Workspace sources overlap at {sorted(overlap)[:5]}; refusing to merge them"
            )
        return WorkspaceManifest({**self.entries, **other.entries})


def read_workspace_manifest(archive: IO[bytes]) -> WorkspaceManifest:
    """Read a tar archive's manifest; content is hashed, hard links resolve to it."""
    entries: dict[str, tuple[str, int, str]] = {}
    try:
        with tarfile.open(fileobj=archive, mode="r|") as tar:
            for member in tar:
                path = _manifest_path(member.name)
                if not path:
                    continue
                if member.isfile():
                    entries[path] = ("file", member.size, _hash_member(tar, member))
                elif member.isdir():
                    entries[path] = ("dir", 0, "")
                elif member.issym():
                    entries[path] = ("symlink", 0, member.linkname)
                elif member.islnk():
                    target = entries.get(_manifest_path(member.linkname))
                    if target is None or target[0] != "file":
                        raise WorkspaceVerificationError(
                            f"Archive hard link {path} names {member.linkname}, "
                            "which is not a file earlier in the archive"
                        )
                    entries[path] = target
                else:
                    entries[path] = ("special", 0, "")
    except tarfile.TarError as exc:
        raise WorkspaceVerificationError(f"Workspace archive is not a readable tar: {exc}") from exc
    return WorkspaceManifest(entries)


def _manifest_path(name: str) -> str:
    path = name
    while path.startswith("./"):
        path = path[2:]
    return path.strip("/") if path != "." else ""


def _hash_member(tar: tarfile.TarFile, member: tarfile.TarInfo) -> str:
    digest = hashlib.sha256()
    extracted = tar.extractfile(member)
    if extracted is None:
        return digest.hexdigest()
    while chunk := extracted.read(_HASH_CHUNK_BYTES):
        digest.update(chunk)
    return digest.hexdigest()
