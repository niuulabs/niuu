"""Local mount contributor — hostPath volumes for local filesystem mounts."""

from __future__ import annotations

import logging

from volundr.domain.models import LocalMountSource, PodSpecAdditions, Session
from volundr.domain.mount_policy import ensure_host_path_allowed
from volundr.domain.ports import SessionContext, SessionContribution, SessionContributor

logger = logging.getLogger(__name__)


class LocalMountContributor(SessionContributor):
    """Contributes hostPath volumes for local mount sources.

    Validates mount paths against allowed_prefixes and allow_root_mount
    config. Only activates when the session source is LocalMountSource.
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        allow_root_mount: bool = False,
        allowed_prefixes: list[str] | None = None,
        **_extra: object,
    ):
        self._enabled = enabled
        self._allow_root_mount = allow_root_mount
        self._allowed_prefixes = allowed_prefixes or []

    @property
    def name(self) -> str:
        return "local_mount"

    async def contribute(
        self,
        session: Session,
        context: SessionContext,
    ) -> SessionContribution:
        if not isinstance(session.source, LocalMountSource):
            return SessionContribution()

        if not self._enabled:
            logger.warning(
                "Local mounts disabled but session %s requested local_mount source",
                session.id,
            )
            return SessionContribution()

        volumes: list[dict] = []
        mounts: list[dict] = []
        for i, mapping in enumerate(session.source.paths):
            self._validate_host_path(mapping.host_path)

            vol_name = f"local-mount-{i}"
            volumes.append(
                {
                    "name": vol_name,
                    "hostPath": {"path": mapping.host_path, "type": "Directory"},
                }
            )
            mounts.append(
                {
                    "name": vol_name,
                    "mountPath": mapping.mount_path,
                    "readOnly": mapping.read_only,
                }
            )

        pod_spec = PodSpecAdditions(
            volumes=tuple(volumes),
            volume_mounts=tuple(mounts),
        )

        return SessionContribution(pod_spec=pod_spec)

    def _validate_host_path(self, host_path: str) -> None:
        """Validate a host path against security constraints.

        Raises:
            ValueError: If the path is not allowed.
        """
        ensure_host_path_allowed(
            host_path,
            self._allowed_prefixes,
            allow_root_mount=self._allow_root_mount,
        )
