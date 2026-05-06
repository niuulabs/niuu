"""Shared contract tests for FlockFlowProvider implementations.

Each provider test module subclasses ``FlockFlowProviderContract`` and
implements the ``make_provider`` fixture so the same behavioural assertions
run against every adapter.
"""

from __future__ import annotations

from tyr.domain.flock_flow import FlockFlowConfig, FlockPersonaOverride
from tyr.ports.flock_flow import FlockFlowProvider


def make_flow(name: str = "test-flow") -> FlockFlowConfig:
    return FlockFlowConfig(
        name=name,
        description="A test flow",
        personas=[
            FlockPersonaOverride(name="coordinator"),
            FlockPersonaOverride(name="reviewer", llm={"model": "claude-opus-4-6"}),
        ],
        mesh_transport="nng",
        mimir_hosted_url="http://mimir:8080",
        sleipnir_publish_urls=["http://sleipnir:4222"],
        max_concurrent_tasks=5,
    )


class FlockFlowProviderContract:
    """Contract tests that every FlockFlowProvider must satisfy.

    Subclasses MUST define a ``provider`` pytest fixture that returns a
    fresh, empty ``FlockFlowProvider`` instance.
    """

    def test_implements_port(self, provider: FlockFlowProvider) -> None:
        assert isinstance(provider, FlockFlowProvider)

    def test_empty_by_default(self, provider: FlockFlowProvider) -> None:
        assert provider.list() == []
        assert provider.get("nonexistent") is None

    def test_save_and_get(self, provider: FlockFlowProvider) -> None:
        flow = make_flow()
        provider.save(flow)

        result = provider.get("test-flow")
        assert result is not None
        assert result.name == "test-flow"
        assert result.description == "A test flow"
        assert len(result.personas) == 2
        assert result.personas[0].name == "coordinator"
        assert result.personas[1].llm == {"model": "claude-opus-4-6"}
        assert result.max_concurrent_tasks == 5

    def test_save_overwrites(self, provider: FlockFlowProvider) -> None:
        provider.save(make_flow())

        updated = FlockFlowConfig(name="test-flow", description="Updated")
        provider.save(updated)

        result = provider.get("test-flow")
        assert result is not None
        assert result.description == "Updated"

    def test_list(self, provider: FlockFlowProvider) -> None:
        provider.save(make_flow("flow-a"))
        provider.save(make_flow("flow-b"))

        flows = provider.list()
        names = {f.name for f in flows}
        assert names == {"flow-a", "flow-b"}

    def test_delete_existing(self, provider: FlockFlowProvider) -> None:
        provider.save(make_flow())

        deleted = provider.delete("test-flow")
        assert deleted is True
        assert provider.get("test-flow") is None

    def test_delete_nonexistent(self, provider: FlockFlowProvider) -> None:
        deleted = provider.delete("nonexistent")
        assert deleted is False
