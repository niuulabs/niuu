"""Tests for ConfigFlockFlowProvider — contract + YAML round-trip + runtime additions."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tests.test_tyr.test_flock_flows.contract import (
    FlockFlowProviderContract,
    make_flow,
)
from tyr.adapters.flows.config import ConfigFlockFlowProvider
from tyr.domain.flock_flow import FlockFlowConfig


class TestConfigFlockFlowProvider(FlockFlowProviderContract):
    """Runs the shared contract suite against ConfigFlockFlowProvider."""

    @pytest.fixture()
    def provider(self) -> ConfigFlockFlowProvider:
        return ConfigFlockFlowProvider()


class TestConfigFlockFlowProviderYAML:
    """YAML file loading tests."""

    def test_loads_from_yaml(self, tmp_path: Path) -> None:
        yaml_content = textwrap.dedent("""\
            - name: code-review-flow
              description: Standard code review
              personas:
                - name: coordinator
                - name: reviewer
                  llm:
                    model: claude-opus-4-6
              mesh_transport: nng
              mimir_hosted_url: http://mimir:8080
              max_concurrent_tasks: 3
            - name: security-audit
              description: Security-focused review
              personas:
                - name: security-auditor
                  system_prompt_extra: Focus on OWASP top 10
                  iteration_budget: 10
        """)
        yaml_file = tmp_path / "flows.yaml"
        yaml_file.write_text(yaml_content)

        provider = ConfigFlockFlowProvider(path=str(yaml_file))

        assert len(provider.list()) == 2

        flow = provider.get("code-review-flow")
        assert flow is not None
        assert flow.description == "Standard code review"
        assert len(flow.personas) == 2
        assert flow.personas[1].llm == {"model": "claude-opus-4-6"}

        audit = provider.get("security-audit")
        assert audit is not None
        assert audit.personas[0].system_prompt_extra == "Focus on OWASP top 10"
        assert audit.personas[0].iteration_budget == 10

    def test_runtime_additions_after_yaml_load(self, tmp_path: Path) -> None:
        yaml_content = textwrap.dedent("""\
            - name: initial-flow
              personas:
                - name: coordinator
        """)
        yaml_file = tmp_path / "flows.yaml"
        yaml_file.write_text(yaml_content)

        provider = ConfigFlockFlowProvider(path=str(yaml_file))
        provider.save(make_flow("runtime-flow"))

        assert len(provider.list()) == 2
        assert provider.get("initial-flow") is not None
        assert provider.get("runtime-flow") is not None

    def test_missing_file_is_warning(self) -> None:
        provider = ConfigFlockFlowProvider(path="/nonexistent/path.yaml")
        assert provider.list() == []

    def test_empty_path_no_load(self) -> None:
        provider = ConfigFlockFlowProvider(path="")
        assert provider.list() == []

    def test_invalid_yaml_format(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "bad.yaml"
        yaml_file.write_text("not_a_list: true")
        provider = ConfigFlockFlowProvider(path=str(yaml_file))
        assert provider.list() == []

    def test_yaml_round_trip(self, tmp_path: Path) -> None:
        """Test that to_dict -> from_dict round-trips correctly."""
        original = make_flow("roundtrip")
        d = original.to_dict()
        restored = FlockFlowConfig.from_dict(d)

        assert restored.name == original.name
        assert restored.description == original.description
        assert len(restored.personas) == len(original.personas)
        assert restored.personas[1].llm == original.personas[1].llm
        assert restored.mimir == original.mimir
        assert restored.mimir_hosted_url == original.mimir_hosted_url
        assert restored.sleipnir_publish_urls == original.sleipnir_publish_urls
        assert restored.max_concurrent_tasks == original.max_concurrent_tasks
