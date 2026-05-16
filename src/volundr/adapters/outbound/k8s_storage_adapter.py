"""Kubernetes storage adapter for PVC lifecycle management.

Creates and deletes PersistentVolumeClaims via the Kubernetes API.
Drop-in replacement for InMemoryStorageAdapter -- selected via
the dynamic adapter pattern in config YAML.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from uuid import UUID

from volundr.domain.models import (
    ANNOT_WORKSPACE_NAME,
    ANNOT_WORKSPACE_SOURCE_REF,
    ANNOT_WORKSPACE_SOURCE_URL,
    LABEL_MANAGED_BY,
    LABEL_OWNER,
    LABEL_PVC_TYPE,
    LABEL_SESSION_ID,
    LABEL_TENANT_ID,
    LABEL_WORKSPACE_STATUS,
    PVCRef,
    StorageQuota,
    Workspace,
    WorkspaceStatus,
)
from volundr.domain.ports import StoragePort

logger = logging.getLogger(__name__)

# Regex to extract session ID from PVC name
_SESSION_ID_RE = re.compile(r"volundr-session-(.+)-workspace")


class K8sStorageAdapter(StoragePort):
    """Kubernetes implementation of StoragePort.

    Creates and deletes PVCs via the Kubernetes API.
    Constructor accepts plain kwargs (dynamic adapter pattern).
    """

    def __init__(
        self,
        *,
        namespace: str = "volundr-sessions",
        home_storage_class: str = "volundr-home",
        workspace_storage_class: str = "volundr-workspace",
        home_access_mode: str = "ReadWriteMany",
        workspace_access_mode: str = "ReadWriteOnce",
        home_mount_path: str = "/volundr/home",
        workspace_mount_path: str = "/volundr/sessions",
        workspace_size_gb: int = 2,
        **_extra: object,
    ) -> None:
        self._namespace = namespace
        self._home_storage_class = home_storage_class
        self._workspace_storage_class = workspace_storage_class
        self._home_access_mode = home_access_mode
        self._workspace_access_mode = workspace_access_mode
        self._home_mount_path = home_mount_path
        self._workspace_size_gb = workspace_size_gb
        self._workspace_mount_path = workspace_mount_path
        self._api_client = None

    @property
    def home_mount_path(self) -> str:
        return self._home_mount_path

    @property
    def workspace_mount_path(self) -> str:
        return self._workspace_mount_path

    async def _get_api(self):
        """Lazy-load kubernetes_asyncio CoreV1Api."""
        if self._api_client is not None:
            return self._api_client

        from kubernetes_asyncio import client, config

        try:
            config.load_incluster_config()
        except config.ConfigException:
            await config.load_kube_config()

        self._api_client = client.CoreV1Api()
        return self._api_client

    def _home_pvc_name(self, user_id: str) -> str:
        return f"volundr-user-{user_id}-home"

    def _workspace_pvc_name(self, session_id: str) -> str:
        return f"volundr-session-{session_id}-workspace"

    def _build_pvc_manifest(
        self,
        name: str,
        storage_gb: int,
        storage_class: str,
        access_mode: str,
        labels: dict[str, str],
    ) -> dict:
        """Build a PVC manifest dict for the Kubernetes API."""
        from kubernetes_asyncio import client

        return client.V1PersistentVolumeClaim(
            metadata=client.V1ObjectMeta(
                name=name,
                namespace=self._namespace,
                labels=labels,
            ),
            spec=client.V1PersistentVolumeClaimSpec(
                access_modes=[access_mode],
                storage_class_name=storage_class,
                resources=client.V1VolumeResourceRequirements(
                    requests={"storage": f"{storage_gb}Gi"},
                ),
            ),
        )

    def _pvc_to_workspace(self, pvc) -> Workspace:
        """Convert a K8s PVC object to a Workspace domain model."""
        labels = pvc.metadata.labels or {}
        annotations = pvc.metadata.annotations or {}
        session_id_str = labels.get(LABEL_SESSION_ID, "")

        # Parse storage size from spec
        storage_str = ""
        if pvc.spec and pvc.spec.resources and pvc.spec.resources.requests:
            storage_str = pvc.spec.resources.requests.get("storage", "0Gi")
        size_gb = int(storage_str.replace("Gi", "")) if storage_str else 0

        # Derive status from label
        status_str = labels.get(LABEL_WORKSPACE_STATUS, "active")
        try:
            status = WorkspaceStatus(status_str)
        except ValueError:
            status = WorkspaceStatus.ACTIVE

        # Use PVC UID as workspace ID
        pvc_uid = pvc.metadata.uid or "00000000-0000-0000-0000-000000000000"

        # Parse session ID (UUID)
        try:
            session_id = UUID(session_id_str)
        except (ValueError, AttributeError):
            # Extract from PVC name as fallback
            match = _SESSION_ID_RE.match(pvc.metadata.name or "")
            session_id = UUID(match.group(1)) if match else UUID(int=0)

        created_at = pvc.metadata.creation_timestamp

        return Workspace(
            id=UUID(pvc_uid),
            session_id=session_id,
            user_id=labels.get(LABEL_OWNER, ""),
            tenant_id=labels.get(LABEL_TENANT_ID, ""),
            pvc_name=pvc.metadata.name,
            status=status,
            size_gb=size_gb,
            created_at=created_at,
            name=annotations.get(ANNOT_WORKSPACE_NAME),
            source_url=annotations.get(ANNOT_WORKSPACE_SOURCE_URL),
            source_ref=annotations.get(ANNOT_WORKSPACE_SOURCE_REF),
        )

    async def _label_workspace(
        self,
        session_id: str,
        status: WorkspaceStatus,
    ) -> None:
        """Patch the workspace-status label on a PVC."""
        api = await self._get_api()
        name = self._workspace_pvc_name(session_id)
        body = {"metadata": {"labels": {LABEL_WORKSPACE_STATUS: status.value}}}

        try:
            await api.patch_namespaced_persistent_volume_claim(
                name=name,
                namespace=self._namespace,
                body=body,
            )
        except Exception as exc:
            if "404" in str(exc) or "NotFound" in str(exc):
                logger.debug("PVC %s not found for label update", name)
                return
            raise

    async def provision_user_storage(
        self,
        user_id: str,
        quota: StorageQuota,
    ) -> PVCRef:
        """Create a home PVC for a user. Idempotent."""
        api = await self._get_api()
        name = self._home_pvc_name(user_id)
        labels = {
            LABEL_OWNER: user_id,
            LABEL_PVC_TYPE: "home",
            LABEL_MANAGED_BY: "volundr",
        }

        pvc = self._build_pvc_manifest(
            name=name,
            storage_gb=quota.home_gb,
            storage_class=self._home_storage_class,
            access_mode=self._home_access_mode,
            labels=labels,
        )

        try:
            await api.create_namespaced_persistent_volume_claim(
                namespace=self._namespace,
                body=pvc,
            )
            logger.info(
                "Created home PVC %s in namespace %s",
                name,
                self._namespace,
            )
        except Exception as exc:
            err_str = str(exc)
            if "409" in err_str or "AlreadyExists" in err_str:
                logger.info("Home PVC %s already exists, returning existing", name)
            else:
                raise

        return PVCRef(name=name, namespace=self._namespace)

    async def create_session_workspace(
        self,
        session_id: str,
        user_id: str = "",
        tenant_id: str = "",
        workspace_gb: int | None = None,
        name: str | None = None,
        source_url: str | None = None,
        source_ref: str | None = None,
    ) -> PVCRef:
        """Create a workspace PVC for a session."""
        api = await self._get_api()
        size = workspace_gb if workspace_gb is not None else self._workspace_size_gb
        pvc_name = self._workspace_pvc_name(session_id)
        labels = {
            LABEL_SESSION_ID: session_id,
            LABEL_PVC_TYPE: "workspace",
            LABEL_MANAGED_BY: "volundr",
            LABEL_WORKSPACE_STATUS: WorkspaceStatus.ACTIVE.value,
        }
        if user_id:
            labels[LABEL_OWNER] = user_id
        if tenant_id:
            labels[LABEL_TENANT_ID] = tenant_id

        pvc = self._build_pvc_manifest(
            name=pvc_name,
            storage_gb=size,
            storage_class=self._workspace_storage_class,
            access_mode=self._workspace_access_mode,
            labels=labels,
        )

        # Store workspace metadata as annotations so it persists
        # independently of the session record.
        annotations = {}
        if name:
            annotations[ANNOT_WORKSPACE_NAME] = name
        if source_url:
            annotations[ANNOT_WORKSPACE_SOURCE_URL] = source_url
        if source_ref:
            annotations[ANNOT_WORKSPACE_SOURCE_REF] = source_ref
        if annotations:
            pvc.metadata.annotations = annotations

        await api.create_namespaced_persistent_volume_claim(
            namespace=self._namespace,
            body=pvc,
        )
        logger.info(
            "Created workspace PVC %s in namespace %s",
            pvc_name,
            self._namespace,
        )

        return PVCRef(name=pvc_name, namespace=self._namespace)

    async def archive_session_workspace(
        self,
        session_id: str,
    ) -> None:
        """Archive a session's workspace PVC by setting status label."""
        await self._label_workspace(session_id, WorkspaceStatus.ARCHIVED)
        logger.info(
            "Archived workspace PVC for session %s (PVC retained)",
            session_id,
        )

    async def delete_workspace(
        self,
        session_id: str,
    ) -> None:
        """Permanently delete a session's workspace PVC. No-op if not found."""
        api = await self._get_api()
        name = self._workspace_pvc_name(session_id)

        try:
            await api.delete_namespaced_persistent_volume_claim(
                name=name,
                namespace=self._namespace,
            )
            logger.info("Deleted workspace PVC %s", name)
        except Exception as exc:
            if "404" in str(exc) or "NotFound" in str(exc):
                logger.debug(
                    "Workspace PVC %s not found, treating as already deleted",
                    name,
                )
                return
            raise

    async def get_user_storage_usage(
        self,
        user_id: str,
    ) -> int:
        """Get total storage in GB currently in use by a user."""
        workspaces = await self.list_workspaces(user_id)
        return sum(ws.size_gb for ws in workspaces)

    async def deprovision_user_storage(
        self,
        user_id: str,
    ) -> None:
        """Delete a user's home PVC. No-op if not found."""
        api = await self._get_api()
        name = self._home_pvc_name(user_id)

        try:
            await api.delete_namespaced_persistent_volume_claim(
                name=name,
                namespace=self._namespace,
            )
            logger.info("Deleted home PVC %s", name)
        except Exception as exc:
            if "404" in str(exc) or "NotFound" in str(exc):
                logger.debug(
                    "Home PVC %s not found, treating as already deleted",
                    name,
                )
                return
            raise

    async def list_workspaces(
        self,
        user_id: str,
        status: WorkspaceStatus | None = None,
    ) -> list[Workspace]:
        """List workspace PVCs for a user by label selector."""
        api = await self._get_api()
        selector = f"{LABEL_MANAGED_BY}=volundr,{LABEL_PVC_TYPE}=workspace,{LABEL_OWNER}={user_id}"
        if status is not None:
            selector += f",{LABEL_WORKSPACE_STATUS}={status.value}"

        result = await api.list_namespaced_persistent_volume_claim(
            namespace=self._namespace,
            label_selector=selector,
        )

        return sorted(
            [self._pvc_to_workspace(pvc) for pvc in result.items],
            key=lambda ws: ws.created_at or datetime.min,
            reverse=True,
        )

    async def list_all_workspaces(
        self,
        status: WorkspaceStatus | None = None,
    ) -> list[Workspace]:
        """List all workspace PVCs by label selector."""
        api = await self._get_api()
        selector = f"{LABEL_MANAGED_BY}=volundr,{LABEL_PVC_TYPE}=workspace"
        if status is not None:
            selector += f",{LABEL_WORKSPACE_STATUS}={status.value}"

        result = await api.list_namespaced_persistent_volume_claim(
            namespace=self._namespace,
            label_selector=selector,
        )

        return sorted(
            [self._pvc_to_workspace(pvc) for pvc in result.items],
            key=lambda ws: ws.created_at or datetime.min,
            reverse=True,
        )

    async def get_workspace_by_session(
        self,
        session_id: str,
    ) -> Workspace | None:
        """Get a workspace PVC by session ID."""
        api = await self._get_api()
        name = self._workspace_pvc_name(session_id)

        try:
            pvc = await api.read_namespaced_persistent_volume_claim(
                name=name,
                namespace=self._namespace,
            )
            return self._pvc_to_workspace(pvc)
        except Exception as exc:
            if "404" in str(exc) or "NotFound" in str(exc):
                return None
            raise

    async def close(self) -> None:
        """Close the Kubernetes API client."""
        if self._api_client is not None:
            await self._api_client.api_client.close()
            self._api_client = None
