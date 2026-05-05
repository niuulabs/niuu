"""Domain models for flock flow configurations.

A FlockFlowConfig is a reusable, named composition of personas that can be
referenced by dispatches and pipelines instead of repeating persona lists
inline.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FlockPersonaOverride:
    """Per-persona overrides within a flow definition."""

    name: str
    llm: dict | None = None
    system_prompt_extra: str = ""
    iteration_budget: int = 0
    max_concurrent_tasks: int = 0

    def to_dict(self) -> dict:
        """Serialize to the wire dict format consumed by workload_config.personas."""
        d: dict = {"name": self.name}
        if self.llm:
            d["llm"] = dict(self.llm)
        if self.system_prompt_extra:
            d["system_prompt_extra"] = self.system_prompt_extra
        if self.iteration_budget:
            d["iteration_budget"] = self.iteration_budget
        if self.max_concurrent_tasks:
            d["max_concurrent_tasks"] = self.max_concurrent_tasks
        return d


@dataclass
class FlockFlowConfig:
    """A named, reusable flock persona composition."""

    name: str
    description: str = ""
    personas: list[FlockPersonaOverride] = field(default_factory=list)
    mesh_transport: str = "nng"
    mimir: dict = field(default_factory=dict)
    mimir_hosted_url: str = ""
    sleipnir_publish_urls: list[str] = field(default_factory=list)
    max_concurrent_tasks: int = 3

    def to_dict(self) -> dict:
        """Serialize to a plain dict for YAML/JSON round-tripping."""
        return {
            "name": self.name,
            "description": self.description,
            "personas": [p.to_dict() for p in self.personas],
            "mesh_transport": self.mesh_transport,
            "mimir": dict(self.mimir),
            "mimir_hosted_url": self.mimir_hosted_url,
            "sleipnir_publish_urls": list(self.sleipnir_publish_urls),
            "max_concurrent_tasks": self.max_concurrent_tasks,
        }

    @classmethod
    def from_dict(cls, data: dict) -> FlockFlowConfig:
        """Deserialize from a plain dict."""
        personas = [
            FlockPersonaOverride(
                name=p["name"],
                llm=p.get("llm"),
                system_prompt_extra=p.get("system_prompt_extra", ""),
                iteration_budget=p.get("iteration_budget", 0),
                max_concurrent_tasks=p.get("max_concurrent_tasks", 0),
            )
            for p in data.get("personas", [])
        ]
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            personas=personas,
            mesh_transport=data.get("mesh_transport", "nng"),
            mimir=dict(data.get("mimir") or {}),
            mimir_hosted_url=data.get("mimir_hosted_url", ""),
            sleipnir_publish_urls=data.get("sleipnir_publish_urls", []),
            max_concurrent_tasks=data.get("max_concurrent_tasks", 3),
        )
