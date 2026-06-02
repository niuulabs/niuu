"""Configuration-driven adapter for system-scope launch specs.

System launch specs are loaded from YAML config (or CRDs in future) and are
read-only. Replaces the former ConfigProfileProvider + ConfigTemplateProvider.
"""

from __future__ import annotations

from volundr.config import LaunchSpecConfig
from volundr.domain.models import LaunchScope, LaunchSpec
from volundr.domain.ports import LaunchSpecProvider


class ConfigLaunchSpecProvider(LaunchSpecProvider):
    """Reads system-scope launch specs from application configuration."""

    def __init__(self, configs: list[LaunchSpecConfig]):
        self._specs: dict[str, LaunchSpec] = {}
        for cfg in configs:
            self._specs[cfg.name] = LaunchSpec(
                name=cfg.name,
                scope=LaunchScope.SYSTEM,
                description=cfg.description,
                is_default=cfg.is_default,
                session_definition=cfg.session_definition,
                workload_type=cfg.workload_type,
                model=cfg.model,
                system_prompt=cfg.system_prompt,
                resource_config=cfg.resource_config,
                mcp_servers=cfg.mcp_servers,
                env_vars=cfg.env_vars,
                env_secret_refs=cfg.env_secret_refs,
                workload_config=cfg.workload_config,
                repos=cfg.repos,
                setup_scripts=cfg.setup_scripts,
                workspace_layout=cfg.workspace_layout,
                cli_tool=cfg.cli_tool,
            )

    def get(self, name: str) -> LaunchSpec | None:
        return self._specs.get(name)

    def list(self, workload_type: str | None = None) -> list[LaunchSpec]:
        specs = list(self._specs.values())
        if workload_type is not None:
            specs = [s for s in specs if s.workload_type == workload_type]
        return sorted(specs, key=lambda s: s.name)

    def get_default(self, workload_type: str) -> LaunchSpec | None:
        for spec in self._specs.values():
            if spec.workload_type == workload_type and spec.is_default:
                return spec
        return None
