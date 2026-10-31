"""Launch-spec contributor — merges a launch spec's runtime config into values.

Replaces TemplateContributor. Resolves the session's launch spec (by name, from
the system provider) and applies its runtime config; falls back to the default
system spec. The merge semantics are identical to the former template/profile
contributor so launch behaviour is unchanged.
"""

import logging
from typing import Any

from volundr.domain.models import LaunchSpec, Session
from volundr.domain.ports import (
    LaunchSpecProvider,
    SessionContext,
    SessionContribution,
    SessionContributor,
)

logger = logging.getLogger(__name__)


class LaunchSpecContributor(SessionContributor):
    """Merges a launch spec's runtime config into Helm values."""

    def __init__(
        self,
        *,
        launch_spec_provider: LaunchSpecProvider | None = None,
        **_extra: object,
    ):
        self._provider = launch_spec_provider

    @property
    def name(self) -> str:
        return "launch_spec"

    async def contribute(
        self,
        session: Session,
        context: SessionContext,
    ) -> SessionContribution:
        values: dict[str, Any] = {}
        spec = self._resolve(context.launch_spec)
        if spec is None:
            return SessionContribution(values=values)
        self._apply(values, spec)
        return SessionContribution(values=values)

    def _resolve(self, name: str | None) -> LaunchSpec | None:
        if self._provider is None:
            return None
        if name:
            spec = self._provider.get(name)
            if spec is not None:
                logger.info("Using launch spec: %s", name)
                return spec
            logger.warning("Launch spec not found: %s, trying default", name)
        default = self._provider.get_default("session")
        if default is not None:
            logger.info("Using default launch spec: %s", default.name)
        return default

    @staticmethod
    def _apply(values: dict, source: LaunchSpec) -> None:
        """Merge runtime config from a launch spec into values.

        A K8s-native resource config (``requests``/``limits``) passes through as
        ``resources``. The user-facing form (``cpu``/``memory``/``gpu``) is what
        the launch wizard reads from a spec and sends with the launch, where
        ResourceContributor translates it; copying it here put ``cpu`` and
        ``memory`` straight into the container's ``resources``, fields
        Kubernetes ignores with a warning on every session pod.
        """
        if _is_k8s_native_resources(source.resource_config):
            values["resources"] = source.resource_config
        if source.env_vars:
            # `env` is not a key the skuld chart renders — only `envVars`, as a
            # list of {name, value}. Emitting the raw mapping here meant a
            # launch spec's env silently never reached the pod.
            values["envVars"] = [
                {"name": str(name), "value": str(value)} for name, value in source.env_vars.items()
            ]
        if source.env_secret_refs:
            values["envSecretRefs"] = source.env_secret_refs
        if source.mcp_servers:
            values["mcpServers"] = source.mcp_servers
        if source.system_prompt:
            values.setdefault("session", {})["systemPrompt"] = source.system_prompt
        if source.workload_config:
            for key, value in source.workload_config.items():
                values.setdefault(key, value)


_K8S_RESOURCE_KEYS = frozenset({"requests", "limits"})


def _is_k8s_native_resources(resource_config: dict) -> bool:
    return bool(resource_config) and set(resource_config) <= _K8S_RESOURCE_KEYS
