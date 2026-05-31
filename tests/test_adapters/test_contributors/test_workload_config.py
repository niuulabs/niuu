"""Tests for WorkloadConfigContributor."""

from volundr.adapters.outbound.contributors.workload_config import WorkloadConfigContributor
from volundr.domain.models import GitSource, Session
from volundr.domain.ports import SessionContext


def _session() -> Session:
    return Session(name="test-session", model="claude-sonnet-4-20250514", source=GitSource())


class TestWorkloadConfigContributor:
    async def test_name(self):
        contributor = WorkloadConfigContributor()
        assert contributor.name == "workload_config"

    async def test_merges_broker_runtime_override(self):
        contributor = WorkloadConfigContributor()
        result = await contributor.contribute(
            _session(),
            SessionContext(workload_config={"broker": {"skipPermissions": False}}),
        )

        assert result.values == {"broker": {"skipPermissions": False}}

    async def test_ignores_legacy_permission_mode(self):
        contributor = WorkloadConfigContributor()
        result = await contributor.contribute(
            _session(),
            SessionContext(workload_config={"permission": {"mode": "auto-review"}}),
        )

        assert result.values == {}

    async def test_explicit_broker_config_wins_over_legacy_permission_mode(self):
        contributor = WorkloadConfigContributor()
        result = await contributor.contribute(
            _session(),
            SessionContext(
                workload_config={
                    "permission": {"mode": "restricted"},
                    "broker": {
                        "skipPermissions": True,
                        "approvalPolicy": "never",
                        "sandbox": "danger-full-access",
                    },
                }
            ),
        )

        assert result.values == {
            "broker": {
                "skipPermissions": True,
                "approvalPolicy": "never",
                "sandbox": "danger-full-access",
            }
        }

    async def test_ignores_unsafe_broker_runtime_override(self):
        contributor = WorkloadConfigContributor()
        result = await contributor.contribute(
            _session(),
            SessionContext(
                workload_config={
                    "broker": {
                        "skipPermissions": False,
                        "transportAdapter": "malicious.Adapter",
                        "env": {"X": "Y"},
                    }
                }
            ),
        )

        assert result.values == {"broker": {"skipPermissions": False}}

    async def test_ignores_non_runtime_workload_config(self):
        contributor = WorkloadConfigContributor()
        result = await contributor.contribute(
            _session(),
            SessionContext(workload_config={"personas": [{"name": "reviewer"}]}),
        )

        assert result.values == {}
