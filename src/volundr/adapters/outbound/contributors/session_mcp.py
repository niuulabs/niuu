"""Generic session MCP contributor for attached session resources."""

from __future__ import annotations

import logging
import re
from typing import Any

from volundr.adapters.outbound.contributors.ravn_flock import (
    _MIMIR_MOUNT_PATH,
    _MIMIR_VOLUME_NAME,
    _normalize_mimir_workload_config,
    _resolve_mimir_runtime,
)
from volundr.domain.models import ForgeProfile, PodSpecAdditions, Session, WorkspaceTemplate
from volundr.domain.ports import (
    ProfileProvider,
    SessionContext,
    SessionContribution,
    SessionContributor,
    TemplateProvider,
)

logger = logging.getLogger(__name__)

_DEFAULT_PYTHON_BINARY = "python3"


def _slugify_server_name(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.strip()).strip("-").lower()
    return slug or "resource"


def _describe_mimir_instance(instance: dict[str, Any]) -> str:
    role = str(instance.get("role") or "shared")
    categories = instance.get("categories") or []
    parts = [f"Mimir knowledge mount '{instance['name']}'", f"role: {role}"]
    if categories:
        parts.append("categories: " + ", ".join(str(category) for category in categories))
    return f"{parts[0]} ({'; '.join(parts[1:])})"


class SessionMCPContributor(SessionContributor):
    """Converts attached session resources into MCP server configs.

    The contributor is generic at the session layer: it inspects attached
    resources and emits ``mcpServers`` into session values. Mimir is simply the
    first supported resource type behind this contributor.
    """

    def __init__(
        self,
        *,
        template_provider: TemplateProvider | None = None,
        profile_provider: ProfileProvider | None = None,
        python_binary: str = _DEFAULT_PYTHON_BINARY,
        **_extra: object,
    ) -> None:
        self._template_provider = template_provider
        self._profile_provider = profile_provider
        self._python_binary = python_binary

    @property
    def name(self) -> str:
        return "session_mcp"

    async def contribute(
        self,
        session: Session,
        context: SessionContext,
    ) -> SessionContribution:
        workload_type, workload_config = self._resolve_workload(context)
        if not workload_config:
            return SessionContribution()

        raw_mimir = workload_config.get("mimir")
        mimir_cfg = _normalize_mimir_workload_config(
            raw_mimir if isinstance(raw_mimir, dict) else None,
            workload_config.get("mimir_hosted_url"),
        )
        if not mimir_cfg:
            return SessionContribution()

        instances, _write_routing = _resolve_mimir_runtime(mimir_cfg)
        if not instances:
            return SessionContribution()

        mcp_servers: list[dict[str, Any]] = []
        requires_local_mount = False
        for instance in instances:
            server = self._build_mimir_server(instance)
            if server is None:
                continue
            mcp_servers.append(server)
            path = str(instance.get("path") or "").strip()
            if path == _MIMIR_MOUNT_PATH or path.startswith(f"{_MIMIR_MOUNT_PATH.rstrip('/')}/"):
                requires_local_mount = True

        if not mcp_servers:
            return SessionContribution()

        pod_spec: PodSpecAdditions | None = None
        if requires_local_mount and workload_type == "ravn_flock":
            pod_spec = PodSpecAdditions(
                volume_mounts=(
                    {
                        "name": _MIMIR_VOLUME_NAME,
                        "mountPath": _MIMIR_MOUNT_PATH,
                    },
                ),
            )

        return SessionContribution(values={"mcpServers": mcp_servers}, pod_spec=pod_spec)

    def _resolve_workload(
        self,
        context: SessionContext,
    ) -> tuple[str, dict[str, Any]]:
        if context.workload_config:
            return context.workload_type, context.workload_config

        source = self._resolve_source(context)
        if source is None:
            return context.workload_type, {}
        return source.workload_type, dict(source.workload_config or {})

    def _resolve_source(self, context: SessionContext) -> WorkspaceTemplate | ForgeProfile | None:
        if context.template_name and self._template_provider is not None:
            template = self._template_provider.get(context.template_name)
            if template is not None:
                return template

        if self._profile_provider is None:
            return None

        if context.profile_name:
            profile = self._profile_provider.get(context.profile_name)
            if profile is not None:
                return profile

        if context.workload_type != "session":
            return self._profile_provider.get_default(context.workload_type)
        return None

    def _build_mimir_server(self, instance: dict[str, Any]) -> dict[str, Any] | None:
        mount_name = str(instance.get("name") or "").strip()
        if not mount_name:
            return None

        server_name = f"mimir-{_slugify_server_name(mount_name)}"
        description = _describe_mimir_instance(instance)

        path = str(instance.get("path") or "").strip()
        if path:
            return {
                "name": server_name,
                "type": "stdio",
                "command": self._python_binary,
                "args": ["-m", "mimir", "mcp", "--path", path, "--name", mount_name],
                "env": {},
                "description": description,
            }

        url = str(instance.get("url") or "").strip()
        if url:
            return {
                "name": server_name,
                "type": "http",
                "url": url,
                "description": description,
            }

        logger.debug("Skipping Mimir MCP server for mount %s without path or url", mount_name)
        return None
