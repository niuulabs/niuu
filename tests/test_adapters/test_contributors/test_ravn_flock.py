"""Tests for RavnFlockContributor."""

import json
import logging
from unittest.mock import MagicMock

import pytest
import yaml

from volundr.adapters.outbound.contributors.core import CoreSessionContributor
from volundr.adapters.outbound.contributors.ravn_flock import (
    RavnFlockContributor,
    _gateway_port_for,
    _normalize_instance,
    _normalize_mimir_workload_config,
    _normalize_personas,
    _ports_for,
    _resolve_mimir_runtime,
    _split_workflow_edge_label,
    _string_list,
)
from volundr.domain.models import (
    GitSource,
    LaunchSpec,
    Session,
    SessionSpec,
    WorkloadPersonaOverride,
)
from volundr.domain.ports import SessionContext

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_mounted_config(pod_spec, persona: str) -> str:
    """Extract the YAML written by the init container for *persona*.

    The heredoc format is:
      cat > /etc/ravn/config.yaml <<'__RAVN_EOF__'\\n<yaml>__RAVN_EOF__\\n
    """
    init_name = f"write-ravn-cfg-{persona}"
    for ic in pod_spec.init_containers:
        if ic["name"] == init_name:
            cmd = ic["command"][2]
            open_marker = "'__RAVN_EOF__'\n"
            close_marker = "__RAVN_EOF__\n"
            start = cmd.index(open_marker) + len(open_marker)
            end = cmd.rindex(close_marker)
            return cmd[start:end]
    raise AssertionError(f"init container {init_name!r} not found")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def session():
    return Session(name="test-flock", model="claude-sonnet-4-20250514", source=GitSource())


@pytest.fixture
def flock_template():
    return LaunchSpec(
        name="ravn-flock",
        workload_type="ravn_flock",
        workload_config={
            "personas": ["coordinator", "reviewer"],
            "mesh": {"transport": "nng"},
            "mimir": {"hosted_url": "https://mimir.niuu.internal/api/v1"},
            "sleipnir": {
                "publish_urls": [
                    "http://ting:8080/sleipnir/events",
                    "http://volundr:8000/sleipnir/events",
                ]
            },
        },
    )


@pytest.fixture
def flock_profile():
    return LaunchSpec(
        name="ravn-flock",
        workload_type="ravn_flock",
        workload_config={
            "personas": ["coordinator", "reviewer"],
            "mesh": {"transport": "nng"},
            "mimir": {},
            "sleipnir": {"publish_urls": ["http://ting:8080/sleipnir/events"]},
        },
    )


@pytest.fixture
def session_template():
    return LaunchSpec(
        name="default",
        workload_type="session",
        workload_config={},
    )


# ---------------------------------------------------------------------------
# Port allocation
# ---------------------------------------------------------------------------


class TestPortAllocation:
    def test_ports_for_index_0(self):
        pub, rep, hs = _ports_for(0, 7480)
        assert pub == 7480
        assert rep == 7481
        assert hs == 7580

    def test_ports_for_index_1(self):
        pub, rep, hs = _ports_for(1, 7480)
        assert pub == 7482
        assert rep == 7483
        assert hs == 7581

    def test_ports_for_index_2(self):
        pub, rep, hs = _ports_for(2, 7480)
        assert pub == 7484
        assert rep == 7485
        assert hs == 7582

    def test_gateway_port_for_index_0(self):
        assert _gateway_port_for(0, 7480) == 7680

    def test_gateway_port_for_index_1(self):
        assert _gateway_port_for(1, 7480) == 7681

    def test_no_port_collisions_for_n_ravens(self):
        """Verify skuld + N ravn nodes have unique ports."""
        n_personas = 5
        all_ports: set[int] = set()

        for i in range(n_personas + 1):  # index 0 = skuld, 1..N = ravn
            pub, rep, hs = _ports_for(i, 7480)
            gw = _gateway_port_for(i, 7480)
            for p in (pub, rep, hs, gw):
                assert p not in all_ports, f"Port collision at index {i}: port {p}"
                all_ports.add(p)

    def test_custom_base_port(self):
        pub, rep, hs = _ports_for(0, 8000)
        assert pub == 8000
        assert rep == 8001
        assert hs == 8100


# ---------------------------------------------------------------------------
# Name
# ---------------------------------------------------------------------------


class TestRavnFlockContributorName:
    def test_name(self):
        c = RavnFlockContributor()
        assert c.name == "ravn_flock"


# ---------------------------------------------------------------------------
# Workload type routing
# ---------------------------------------------------------------------------


class TestWorkloadTypeRouting:
    async def test_session_workload_type_returns_empty(self, session, session_template):
        provider = MagicMock()
        provider.get.return_value = session_template
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(launch_spec="default")
        result = await c.contribute(session, ctx)
        assert result.values == {}
        assert result.pod_spec is None

    async def test_ravn_flock_workload_type_contributes(self, session, flock_template):
        provider = MagicMock()
        provider.get.return_value = flock_template
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(launch_spec="ravn-flock")
        result = await c.contribute(session, ctx)
        assert result.values != {} or result.pod_spec is not None

    async def test_no_provider_returns_empty(self, session):
        c = RavnFlockContributor()
        result = await c.contribute(session, SessionContext())
        assert result.values == {}
        assert result.pod_spec is None

    async def test_no_personas_returns_empty(self, session):
        template = LaunchSpec(
            name="no-personas",
            workload_type="ravn_flock",
            workload_config={"personas": []},
        )
        provider = MagicMock()
        provider.get.return_value = template
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(launch_spec="no-personas")
        result = await c.contribute(session, ctx)
        assert result.values == {}
        assert result.pod_spec is None


# ---------------------------------------------------------------------------
# Contributor output — 2 personas
# ---------------------------------------------------------------------------


class TestContributorOutput:
    async def test_openshell_backend_emits_in_sandbox_process_plan(self, session, flock_template):
        provider = MagicMock()
        provider.get.return_value = flock_template
        contributor = RavnFlockContributor(launch_spec_provider=provider)

        result = await contributor.contribute(
            session,
            SessionContext(launch_spec="ravn-flock", runtime_backend="openshell"),
        )

        processes = result.values["openshell"]["processes"]
        assert [process["name"] for process in processes] == [
            "ravn-coordinator",
            "ravn-reviewer",
        ]
        assert processes[0]["command"][:4] == [
            "/opt/niuu/bin/python",
            "-m",
            "ravn",
            "daemon",
        ]
        assert processes[0]["env"]["HOME"] == "/sandbox/workspace"
        assert "NIUU_WORKLOAD_IDENTITY_TOKEN_FILE" not in processes[0]["env"]
        config_path = "/sandbox/.volundr/flock/coordinator.yaml"
        assert config_path in processes[0]["files"]
        assert yaml.safe_load(processes[0]["files"][config_path])["persona"] == "coordinator"

    async def test_two_ravn_containers_produced(self, session, flock_template):
        provider = MagicMock()
        provider.get.return_value = flock_template
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(launch_spec="ravn-flock")
        result = await c.contribute(session, ctx)

        assert result.pod_spec is not None
        assert len(result.pod_spec.extra_containers) == 2

    async def test_ravn_container_names(self, session, flock_template):
        provider = MagicMock()
        provider.get.return_value = flock_template
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(launch_spec="ravn-flock")
        result = await c.contribute(session, ctx)

        names = [ctr["name"] for ctr in result.pod_spec.extra_containers]
        assert "ravn-coordinator" in names
        assert "ravn-reviewer" in names

    async def test_ravn_containers_use_skuld_cli_runtime_image(self, session, flock_template):
        provider = MagicMock()
        provider.get.return_value = flock_template
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(launch_spec="ravn-flock")

        result = await c.contribute(session, ctx)

        for ctr in result.pod_spec.extra_containers:
            assert ctr["image"] == "ghcr.io/niuulabs/skuld:dev"
            assert ctr["command"][:2] == ["python", "-c"]
            assert "'-m'," in ctr["command"][2]
            assert "'ravn'," in ctr["command"][2]
            assert "'daemon'," in ctr["command"][2]
            assert "'--config'," in ctr["command"][2]
            assert "'--persona'," in ctr["command"][2]
            assert "/workspace/.flock/logs" in ctr["command"][2]
            assert "proc.stdout.readline" in ctr["command"][2]
            assert "read(8192)" not in ctr["command"][2]

    async def test_ravn_containers_run_as_workspace_owner(self, session, flock_template):
        provider = MagicMock()
        provider.get.return_value = flock_template
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(launch_spec="ravn-flock")

        result = await c.contribute(session, ctx)

        for ctr in result.pod_spec.extra_containers:
            assert ctr["securityContext"] == {
                "runAsUser": 1000,
                "runAsGroup": 1000,
                "runAsNonRoot": True,
                "allowPrivilegeEscalation": False,
            }

    async def test_ravn_image_can_be_overridden(self, session, flock_template):
        provider = MagicMock()
        provider.get.return_value = flock_template
        c = RavnFlockContributor(
            launch_spec_provider=provider,
            ravn_image="ghcr.io/niuulabs/niuu:1.2.3",
        )
        ctx = SessionContext(launch_spec="ravn-flock")

        result = await c.contribute(session, ctx)

        for ctr in result.pod_spec.extra_containers:
            assert ctr["image"] == "ghcr.io/niuulabs/niuu:1.2.3"
            assert ctr["command"][:2] == ["python", "-c"]
            assert "'ravn'," in ctr["command"][2]
            assert "'daemon'," in ctr["command"][2]

    async def test_skuld_mesh_enabled_in_env(self, session, flock_template):
        provider = MagicMock()
        provider.get.return_value = flock_template
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(launch_spec="ravn-flock")
        result = await c.contribute(session, ctx)

        env_names = {e["name"]: e["value"] for e in result.pod_spec.env}
        assert env_names.get("SKULD__MESH__ENABLED") == "true"
        assert "SKULD__MESH__PEER_ID" in env_names
        assert "SKULD__MESH__NNG__PUB_SUB_ADDRESS" in env_names
        assert "SKULD__MESH__NNG__REQ_REP_ADDRESS" in env_names
        assert env_names["SKULD__MESH__ENABLED"] == "true"
        assert env_names["SKULD__MESH__TRANSPORT"] == "nng"
        assert env_names["SKULD__MESH__PEER_ID"].startswith("skuld-")
        assert env_names["SKULD__MESH__NNG__PUB_SUB_ADDRESS"].endswith(":7480")
        assert env_names["SKULD__MESH__NNG__REQ_REP_ADDRESS"].endswith(":7481")

    async def test_skuld_static_mesh_peers_in_env(self, session, flock_template):
        provider = MagicMock()
        provider.get.return_value = flock_template
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(launch_spec="ravn-flock")
        result = await c.contribute(session, ctx)

        env_names = {e["name"]: e["value"] for e in result.pod_spec.env}
        adapters = json.loads(env_names["SKULD__MESH__ADAPTERS"])
        peers = adapters[0]["peers"]

        assert adapters[0]["adapter"] == "static"
        assert {peer["peer_id"] for peer in peers} == {
            env_names["SKULD__MESH__PEER_ID"],
            "flock-coordinator",
            "flock-reviewer",
        }
        assert {peer["pub_address"] for peer in peers} == {
            "tcp://127.0.0.1:7480",
            "tcp://127.0.0.1:7482",
            "tcp://127.0.0.1:7484",
        }

    async def test_skuld_workflow_trigger_env_present_when_graph_has_trigger(self, session):
        template = LaunchSpec(
            name="workflow-flock",
            workload_type="ravn_flock",
            workload_config={
                "personas": ["coder"],
                "provenance": {
                    "trace_context": {
                        "traceparent": ("00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"),
                        "ignored": "not-w3c",
                    }
                },
                "workflow": {
                    "workflow_id": "wf-1",
                    "name": "Code",
                    "version": "1.0.0",
                    "scope": "user",
                    "graph": {
                        "nodes": [
                            {
                                "id": "trigger-1",
                                "kind": "trigger",
                                "label": "Dispatch",
                                "source": "manual dispatch",
                                "dispatchEvent": "code.requested",
                            }
                        ],
                        "edges": [],
                    },
                },
            },
        )
        provider = MagicMock()
        provider.get.return_value = template
        c = RavnFlockContributor(launch_spec_provider=provider)
        result = await c.contribute(session, SessionContext(launch_spec="workflow-flock"))

        env_names = {e["name"]: e["value"] for e in result.pod_spec.env}
        assert env_names["SKULD__MESH__CONSUMES_EVENT_TYPES"] == "[]"
        assert env_names["SKULD__ROOM__ENABLED"] == "true"
        assert env_names["SKULD__ROOM__MAX_PARTICIPANTS"] == "2"
        assert env_names["SKULD__ROOM__PRESENCE_SWEEP_INTERVAL_S"] == "0"
        assert env_names["SKULD__WORKFLOW_TRIGGER__ENABLED"] == "true"
        assert env_names["SKULD__WORKFLOW_TRIGGER__EVENT_TYPE"] == "code.requested"
        assert env_names["SKULD__WORKFLOW_TRIGGER__NODE_ID"] == "trigger-1"
        assert env_names["SKULD__WORKFLOW__WORKFLOW_ID"] == "wf-1"
        assert env_names["SKULD__WORKFLOW__NAME"] == "Code"
        assert '"trigger-1"' in env_names["SKULD__WORKFLOW__GRAPH"]
        assert json.loads(env_names["SKULD__WORKFLOW__TRACE_CONTEXT"]) == {
            "traceparent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"
        }

    async def test_multiline_initial_context_survives_as_a_single_env_line(self, session):
        """A research brief is prose and always has newlines, but the sandbox
        provisioner rejects env values containing them — an unencoded brief
        failed provisioning outright and the session never started."""
        brief = "Search the repo for error code 7.\n\nReport findings.\r\nEnd."
        template = LaunchSpec(
            name="workflow-flock",
            workload_type="ravn_flock",
            workload_config={
                "personas": ["research-explorer"],
                "initiative_context": brief,
                "workflow": {
                    "workflow_id": "wf-2",
                    "name": "Research",
                    "graph": {"nodes": [], "edges": []},
                },
            },
        )
        provider = MagicMock()
        provider.get.return_value = template
        c = RavnFlockContributor(launch_spec_provider=provider)

        result = await c.contribute(session, SessionContext(launch_spec="workflow-flock"))

        env_names = {e["name"]: e["value"] for e in result.pod_spec.env}
        raw = env_names["SKULD__WORKFLOW__INITIAL_CONTEXT"]
        assert "\n" not in raw
        assert "\r" not in raw
        assert json.loads(raw) == brief

    async def test_skuld_generic_trigger_env_present_for_plain_coordinator_flock(self, session):
        template = LaunchSpec(
            name="plain-flock",
            workload_type="ravn_flock",
            workload_config={
                "personas": ["coordinator", "coder", "reviewer"],
                "initiative_context": "Implement NIU-805",
            },
        )
        provider = MagicMock()
        provider.get.return_value = template
        c = RavnFlockContributor(launch_spec_provider=provider)
        result = await c.contribute(session, SessionContext(launch_spec="plain-flock"))

        env_names = {e["name"]: e["value"] for e in result.pod_spec.env}
        assert env_names["SKULD__MESH__CONSUMES_EVENT_TYPES"] == "[]"
        assert env_names["SKULD__WORKFLOW_TRIGGER__ENABLED"] == "true"
        assert env_names["SKULD__WORKFLOW_TRIGGER__EVENT_TYPE"] == "run.requested"
        assert env_names["SKULD__WORKFLOW_TRIGGER__NODE_ID"] == "dispatch-root"

    async def test_mimir_volume_not_added_without_explicit_local(self, session, flock_template):
        provider = MagicMock()
        provider.get.return_value = flock_template
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(launch_spec="ravn-flock")
        result = await c.contribute(session, ctx)

        volume_names = [v["name"] for v in result.pod_spec.volumes]
        assert "mimir-local" not in volume_names

    async def test_ravn_container_omits_local_mimir_mount_without_explicit_local(
        self, session, flock_template
    ):
        provider = MagicMock()
        provider.get.return_value = flock_template
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(launch_spec="ravn-flock")
        result = await c.contribute(session, ctx)

        ravn_ctr = result.pod_spec.extra_containers[0]
        mount_paths = {m["mountPath"] for m in ravn_ctr["volumeMounts"]}
        assert "/mimir/local" not in mount_paths

    async def test_ravn_container_has_workspace_mount(self, session, flock_template):
        provider = MagicMock()
        provider.get.return_value = flock_template
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(launch_spec="ravn-flock")
        result = await c.contribute(session, ctx)

        ravn_ctr = result.pod_spec.extra_containers[0]
        mount_paths = {m["mountPath"] for m in ravn_ctr["volumeMounts"]}
        assert "/workspace" in mount_paths

    async def test_ravn_containers_share_writable_workspace_mount(self, session, flock_template):
        provider = MagicMock()
        provider.get.return_value = flock_template
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(launch_spec="ravn-flock")
        result = await c.contribute(session, ctx)

        workspace_mounts = []
        for ravn_ctr in result.pod_spec.extra_containers:
            ws_mount = next(m for m in ravn_ctr["volumeMounts"] if m["mountPath"] == "/workspace")
            workspace_mounts.append(ws_mount)
        assert workspace_mounts
        for ws_mount in workspace_mounts:
            assert ws_mount["name"] == "sessions"
            assert ws_mount["subPath"] == f"{session.id}/workspace"
            assert ws_mount.get("readOnly") is False

    async def test_sleipnir_publish_urls_in_skuld_env(self, session, flock_template):
        provider = MagicMock()
        provider.get.return_value = flock_template
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(launch_spec="ravn-flock")
        result = await c.contribute(session, ctx)

        env_names = {e["name"]: e["value"] for e in result.pod_spec.env}
        assert "SLEIPNIR_PUBLISH_URLS" in env_names
        assert "ting:8080" in env_names["SLEIPNIR_PUBLISH_URLS"]

    async def test_sleipnir_publish_urls_in_ravn_env(self, session, flock_template):
        provider = MagicMock()
        provider.get.return_value = flock_template
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(launch_spec="ravn-flock")
        result = await c.contribute(session, ctx)

        for ctr in result.pod_spec.extra_containers:
            env = {e["name"]: e["value"] for e in ctr["env"]}
            assert "SLEIPNIR_PUBLISH_URLS" in env

    async def test_mimir_hosted_url_in_values(self, session, flock_template):
        provider = MagicMock()
        provider.get.return_value = flock_template
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(launch_spec="ravn-flock")
        result = await c.contribute(session, ctx)

        assert (
            result.values.get("mimir", {}).get("hostedUrl") == "https://mimir.niuu.internal/api/v1"
        )

    async def test_richer_mimir_workload_is_preserved_in_values(self, session):
        provider = MagicMock()
        provider.get.return_value = LaunchSpec(
            name="registry-values",
            workload_type="ravn_flock",
            workload_config={
                "personas": ["coordinator"],
                "mimir": {
                    "registry_refs": [{"registry_entry_id": "shared", "mount_name": "shared"}],
                    "ephemeral_locals": [{"mount_name": "scratchpad"}],
                    "bindings": [{"mount_name": "scratchpad", "write_prefixes": ["draft/"]}],
                },
            },
        )
        c = RavnFlockContributor(launch_spec_provider=provider)
        result = await c.contribute(session, SessionContext(launch_spec="registry-values"))

        assert result.values["mimir"]["registryRefs"] == [
            {"registry_entry_id": "shared", "mount_name": "shared"}
        ]
        assert result.values["mimir"]["ephemeralLocals"] == [{"mount_name": "scratchpad"}]
        assert result.values["mimir"]["bindings"] == [
            {"mount_name": "scratchpad", "write_prefixes": ["draft/"]}
        ]

    async def test_mesh_values_present(self, session, flock_template):
        provider = MagicMock()
        provider.get.return_value = flock_template
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(launch_spec="ravn-flock")
        result = await c.contribute(session, ctx)

        assert result.values.get("mesh", {}).get("enabled") is True
        assert result.values["mesh"]["transport"] == "nng"

    async def test_flock_values_preserve_llm_and_persona_overrides(self, session, flock_template):
        provider = MagicMock()
        provider.get.return_value = flock_template
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(
            workload_type="ravn_flock",
            workload_config={
                "personas": [
                    {"name": "coder", "llm": {"model": "Qwen/Qwen3.6-35B-A3B-FP8"}},
                    {
                        "name": "reviewer",
                        "system_prompt_extra": "Be thorough.",
                        "iteration_budget": 40,
                    },
                ],
                "llm_config": {"model": "google/gemma-4-26B-A4B-it"},
                "max_concurrent_tasks": 5,
            },
        )

        result = await c.contribute(session, ctx)

        assert result.values["flock"]["llm_config"]["model"] == "google/gemma-4-26B-A4B-it"
        assert result.values["flock"]["max_concurrent_tasks"] == 5
        assert result.values["flock"]["personas"][0]["name"] == "coder"
        assert result.values["flock"]["personas"][0]["llm"]["model"] == "Qwen/Qwen3.6-35B-A3B-FP8"
        assert result.values["flock"]["personas"][1]["system_prompt_extra"] == "Be thorough."
        assert result.values["flock"]["personas"][1]["iteration_budget"] == 40


# ---------------------------------------------------------------------------
# Mounted config (replaces RAVN_CONFIG_INLINE)
# ---------------------------------------------------------------------------


class TestMountedConfig:
    async def test_ravn_config_inline_absent(self, session, flock_template):
        """RAVN_CONFIG_INLINE must not appear in any container env."""
        provider = MagicMock()
        provider.get.return_value = flock_template
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(launch_spec="ravn-flock")
        result = await c.contribute(session, ctx)

        for ctr in result.pod_spec.extra_containers:
            env_names = {e["name"] for e in ctr["env"]}
            assert "RAVN_CONFIG_INLINE" not in env_names

    async def test_ravn_config_env_points_to_mount(self, session, flock_template):
        """Each sidecar has RAVN_CONFIG=/etc/ravn/config.yaml."""
        provider = MagicMock()
        provider.get.return_value = flock_template
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(launch_spec="ravn-flock")
        result = await c.contribute(session, ctx)

        for ctr in result.pod_spec.extra_containers:
            env = {e["name"]: e["value"] for e in ctr["env"]}
            assert env["RAVN_CONFIG"] == "/etc/ravn/config.yaml"

    async def test_ravn_sidecars_use_workspace_home(self, session, flock_template):
        """Ravn daemon sidecars resolve ~/.ravn under the writable workspace."""
        provider = MagicMock()
        provider.get.return_value = flock_template
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(launch_spec="ravn-flock")
        result = await c.contribute(session, ctx)

        for ctr in result.pod_spec.extra_containers:
            env = {e["name"]: e["value"] for e in ctr["env"]}
            assert env["HOME"] == "/workspace"
            assert env["RAVN_STATE_DIR"] == "/workspace/.ravn"

    async def test_ravn_config_uses_workspace_mount_root(self, session, flock_template):
        """Ravn sidecars must run tools and Codex transports from the writable workspace."""
        provider = MagicMock()
        provider.get.return_value = flock_template
        c = RavnFlockContributor(launch_spec_provider=provider)
        result = await c.contribute(session, SessionContext(launch_spec="ravn-flock"))

        reviewer_cfg = yaml.safe_load(_extract_mounted_config(result.pod_spec, "reviewer"))

        assert reviewer_cfg["permission"]["workspace_root"] == "/workspace"

    async def test_ravn_config_deep_merges_workload_settings(self, session):
        contributor = RavnFlockContributor()
        context = SessionContext(
            workload_type="ravn_flock",
            workload_config={
                "personas": ["reviewer"],
                "ravn_config": {
                    "gateway": {
                        "platform": {
                            "enabled": True,
                            "workload_token_file": "/var/run/secrets/niuu-workload/token",
                            "workload_exchange_url": "https://platform.example/token/exchange",
                        }
                    }
                },
            },
        )

        result = await contributor.contribute(session, context)
        reviewer_cfg = yaml.safe_load(_extract_mounted_config(result.pod_spec, "reviewer"))

        assert reviewer_cfg["gateway"]["enabled"] is True
        assert reviewer_cfg["gateway"]["platform"] == {
            "enabled": True,
            "workload_token_file": "/var/run/secrets/niuu-workload/token",
            "workload_exchange_url": "https://platform.example/token/exchange",
        }

    async def test_observability_config_reaches_ravn_and_skuld_with_stable_names(self, session):
        contributor = RavnFlockContributor()
        context = SessionContext(
            workload_type="ravn_flock",
            workload_config={
                "personas": ["reviewer"],
                "observability": {
                    "enabled": True,
                    "trace_endpoint": "https://tempo.example:443",
                    "metric_endpoint": "https://mimir.example/v1/metrics",
                    "capture_content": True,
                },
            },
        )

        result = await contributor.contribute(session, context)
        reviewer_cfg = yaml.safe_load(_extract_mounted_config(result.pod_spec, "reviewer"))
        skuld_env = {entry["name"]: entry["value"] for entry in result.pod_spec.env}

        assert reviewer_cfg["observability"] == {
            "enabled": True,
            "trace_endpoint": "https://tempo.example:443",
            "metric_endpoint": "https://mimir.example/v1/metrics",
            "capture_content": True,
            "service_name": "ravn",
        }
        assert skuld_env["SKULD__OBSERVABILITY__SERVICE_NAME"] == "skuld"
        assert skuld_env["SKULD__OBSERVABILITY__ENABLED"] == "true"
        assert skuld_env["SKULD__OBSERVABILITY__TRACE_ENDPOINT"] == ("https://tempo.example:443")
        assert skuld_env["SKULD__OBSERVABILITY__METRIC_ENDPOINT"] == (
            "https://mimir.example/v1/metrics"
        )

    async def test_ravn_sidecars_use_unique_service_ports(self, session, flock_template):
        """Each Ravn API server must bind its own port inside the shared pod netns."""
        provider = MagicMock()
        provider.get.return_value = flock_template
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(launch_spec="ravn-flock")
        result = await c.contribute(session, ctx)

        ports: list[str] = []
        for ctr in result.pod_spec.extra_containers:
            env = {e["name"]: e["value"] for e in ctr["env"]}
            assert env["HOST"] == "0.0.0.0"
            ports.append(env["PORT"])

        assert ports == ["7781", "7782"]
        assert "7681" not in ports

    async def test_per_sidecar_config_volume(self, session, flock_template):
        """Each persona gets its own config emptyDir volume."""
        provider = MagicMock()
        provider.get.return_value = flock_template
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(launch_spec="ravn-flock")
        result = await c.contribute(session, ctx)

        vol_names = [v["name"] for v in result.pod_spec.volumes]
        assert "ravn-cfg-coordinator" in vol_names
        assert "ravn-cfg-reviewer" in vol_names

    async def test_per_sidecar_init_container(self, session, flock_template):
        """Each persona gets an init container that writes its config."""
        provider = MagicMock()
        provider.get.return_value = flock_template
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(launch_spec="ravn-flock")
        result = await c.contribute(session, ctx)

        ic_names = [ic["name"] for ic in result.pod_spec.init_containers]
        assert "write-ravn-cfg-coordinator" in ic_names
        assert "write-ravn-cfg-reviewer" in ic_names

    async def test_init_container_writes_to_correct_volume(self, session, flock_template):
        """Init container mounts the matching config volume."""
        provider = MagicMock()
        provider.get.return_value = flock_template
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(launch_spec="ravn-flock")
        result = await c.contribute(session, ctx)

        for ic in result.pod_spec.init_containers:
            persona = ic["name"].replace("write-ravn-cfg-", "")
            vol_name = f"ravn-cfg-{persona}"
            mounts = {m["name"]: m["mountPath"] for m in ic["volumeMounts"]}
            assert mounts[vol_name] == "/etc/ravn"

    async def test_init_container_runs_non_root(self, session, flock_template):
        """Config writer init containers satisfy Skuld's non-root pod policy."""
        provider = MagicMock()
        provider.get.return_value = flock_template
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(launch_spec="ravn-flock")
        result = await c.contribute(session, ctx)

        for ic in result.pod_spec.init_containers:
            assert ic["securityContext"] == {
                "runAsUser": 1000,
                "runAsGroup": 1000,
                "runAsNonRoot": True,
                "allowPrivilegeEscalation": False,
            }

    async def test_sidecar_mounts_config_readonly(self, session, flock_template):
        """Sidecar mounts the config volume read-only at /etc/ravn."""
        provider = MagicMock()
        provider.get.return_value = flock_template
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(launch_spec="ravn-flock")
        result = await c.contribute(session, ctx)

        for ctr in result.pod_spec.extra_containers:
            persona = ctr["name"].replace("ravn-", "")
            cfg_mount = next(m for m in ctr["volumeMounts"] if m["mountPath"] == "/etc/ravn")
            assert cfg_mount["name"] == f"ravn-cfg-{persona}"
            assert cfg_mount.get("readOnly") is True


# ---------------------------------------------------------------------------
# Config generation (via init container command)
# ---------------------------------------------------------------------------


class TestConfigGeneration:
    async def test_mounted_config_has_persona(self, session, flock_template):
        provider = MagicMock()
        provider.get.return_value = flock_template
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(launch_spec="ravn-flock")
        result = await c.contribute(session, ctx)

        for ctr in result.pod_spec.extra_containers:
            env = {e["name"]: e["value"] for e in ctr["env"]}
            assert "RAVN_PEER_ID" in env
            assert env["RAVN_PEER_ID"].startswith("flock-")

            persona = env["RAVN_PERSONA"]
            cfg = _extract_mounted_config(result.pod_spec, persona)
            assert persona in cfg

    async def test_mounted_config_has_mesh_section(self, session, flock_template):
        provider = MagicMock()
        provider.get.return_value = flock_template
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(launch_spec="ravn-flock")
        result = await c.contribute(session, ctx)

        for persona in ("coordinator", "reviewer"):
            cfg = _extract_mounted_config(result.pod_spec, persona)
            assert "mesh:" in cfg
            assert "enabled: true" in cfg

    async def test_mounted_config_has_static_mesh_peers(self, session, flock_template):
        provider = MagicMock()
        provider.get.return_value = flock_template
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(launch_spec="ravn-flock")
        result = await c.contribute(session, ctx)

        for persona in ("coordinator", "reviewer"):
            cfg = yaml.safe_load(_extract_mounted_config(result.pod_spec, persona))
            adapters = cfg["discovery"]["adapters"]
            peers = adapters[0]["peers"]

            assert adapters[0]["adapter"] == "static"
            assert {peer["peer_id"] for peer in peers} == {
                f"skuld-{str(session.id)[:8]}",
                "flock-coordinator",
                "flock-reviewer",
            }
            assert {peer["pub_address"] for peer in peers} == {
                "tcp://127.0.0.1:7480",
                "tcp://127.0.0.1:7482",
                "tcp://127.0.0.1:7484",
            }

    async def test_mounted_config_has_mimir_instances(self, session, flock_template):
        provider = MagicMock()
        provider.get.return_value = flock_template
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(launch_spec="ravn-flock")
        result = await c.contribute(session, ctx)

        for persona in ("coordinator", "reviewer"):
            cfg = _extract_mounted_config(result.pod_spec, persona)
            assert "mimir:" in cfg
            assert "instances:" in cfg
            assert "https://mimir.niuu.internal/api/v1" in cfg

    async def test_mounted_config_has_write_routing(self, session, flock_template):
        provider = MagicMock()
        provider.get.return_value = flock_template
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(launch_spec="ravn-flock")
        result = await c.contribute(session, ctx)

        for persona in ("coordinator", "reviewer"):
            cfg = _extract_mounted_config(result.pod_spec, persona)
            assert "write_routing:" in cfg
            assert "project/" in cfg

    async def test_mounted_config_hosted_url_in_instances(self, session, flock_template):
        provider = MagicMock()
        provider.get.return_value = flock_template
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(launch_spec="ravn-flock")
        result = await c.contribute(session, ctx)

        for persona in ("coordinator", "reviewer"):
            cfg = _extract_mounted_config(result.pod_spec, persona)
            assert "https://mimir.niuu.internal/api/v1" in cfg
            assert "project/" in cfg
            assert "entity/" in cfg

    async def test_mounted_config_no_hosted_url_has_no_instances(self, session, flock_profile):
        """When no Mimir resources are configured, no runtime instances are injected."""
        provider = MagicMock()
        provider.get.return_value = flock_profile
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(launch_spec="ravn-flock")
        result = await c.contribute(session, ctx)

        for persona in ("coordinator", "reviewer"):
            cfg = _extract_mounted_config(result.pod_spec, persona)
            assert "instances: []" in cfg
            assert "project/" not in cfg
            assert "entity/" not in cfg

    async def test_mounted_config_resolves_registry_refs_and_ephemeral_locals(self, session):
        template = LaunchSpec(
            name="registry-flock",
            workload_type="ravn_flock",
            workload_config={
                "personas": ["coordinator"],
                "mimir": {
                    "hosted_url": "https://mimir.niuu.internal/api/v1",
                    "registry_refs": [
                        {
                            "registry_entry_id": "shared-team-mimir",
                            "mount_name": "shared-team-mimir",
                            "categories": ["entity", "decision"],
                            "auth_ref": "integration:volundr",
                        }
                    ],
                    "ephemeral_locals": [
                        {
                            "resource_node_id": "scratch",
                            "mount_name": "scratchpad",
                            "categories": ["draft"],
                        }
                    ],
                    "bindings": [
                        {
                            "mount_name": "shared-team-mimir",
                            "access": "read_write",
                            "write_prefixes": ["project/"],
                        },
                        {
                            "mount_name": "scratchpad",
                            "access": "write",
                            "write_prefixes": ["draft/"],
                        },
                    ],
                },
            },
        )
        provider = MagicMock()
        provider.get.return_value = template
        c = RavnFlockContributor(launch_spec_provider=provider)
        result = await c.contribute(session, SessionContext(launch_spec="registry-flock"))

        cfg = _extract_mounted_config(result.pod_spec, "coordinator")
        parsed = yaml.safe_load(cfg)
        shared = parsed["mimir"]["instances"][0]
        assert shared["name"] == "shared-team-mimir"
        assert shared["url"] == "https://mimir.niuu.internal/api/v1"
        assert shared["auth"] == {
            "type": "workload",
            "token_file": "/var/run/secrets/niuu-workload/token",
            "audiences": ["mimir"],
        }
        assert "scratchpad" in cfg
        assert "/mimir/local/scratchpad" in cfg
        assert "project/" in cfg
        assert "draft/" in cfg
        ravn_container = result.pod_spec.extra_containers[0]
        assert {
            "name": "niuu-workload-identity",
            "mountPath": "/var/run/secrets/niuu-workload",
            "readOnly": True,
        } in ravn_container["volumeMounts"]
        volume_names = [v["name"] for v in result.pod_spec.volumes]
        assert "mimir-local" in volume_names
        mount_paths = {m["mountPath"] for m in result.pod_spec.extra_containers[0]["volumeMounts"]}
        assert "/mimir/local" in mount_paths

    async def test_mounted_config_sleipnir_webhook(self, session, flock_template):
        provider = MagicMock()
        provider.get.return_value = flock_template
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(launch_spec="ravn-flock")
        result = await c.contribute(session, ctx)

        for persona in ("coordinator", "reviewer"):
            cfg = _extract_mounted_config(result.pod_spec, persona)
            assert "sleipnir:" in cfg
            assert "webhook" in cfg


# ---------------------------------------------------------------------------
# NNG port allocation
# ---------------------------------------------------------------------------


class TestNngPortAllocation:
    async def test_ravn_containers_have_nng_ports(self, session, flock_template):
        provider = MagicMock()
        provider.get.return_value = flock_template
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(launch_spec="ravn-flock")
        result = await c.contribute(session, ctx)

        for ctr in result.pod_spec.extra_containers:
            port_nums = {p["containerPort"] for p in ctr["ports"]}
            # Each ravn container must have pub, rep, hs, gw ports
            assert len(port_nums) == 4

    async def test_skuld_and_ravn_ports_do_not_collide(self, session, flock_template):
        provider = MagicMock()
        provider.get.return_value = flock_template
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(launch_spec="ravn-flock")
        result = await c.contribute(session, ctx)

        all_ports: list[int] = []
        # Skuld ports from env
        skuld_env = {e["name"]: e["value"] for e in result.pod_spec.env}
        for key in ("SKULD__MESH__NNG__PUB_SUB_ADDRESS", "SKULD__MESH__NNG__REQ_REP_ADDRESS"):
            addr = skuld_env.get(key, "")
            port = int(addr.rsplit(":", 1)[-1])
            all_ports.append(port)

        # Ravn container ports
        for ctr in result.pod_spec.extra_containers:
            for p in ctr["ports"]:
                all_ports.append(p["containerPort"])

        assert len(all_ports) == len(set(all_ports)), "Port collision detected"


# ---------------------------------------------------------------------------
# Integration: contributor pipeline merge
# ---------------------------------------------------------------------------


class TestContributorPipelineMerge:
    async def test_merge_with_core_contributor(self, session, flock_template):
        template_provider = MagicMock()
        template_provider.get.return_value = flock_template

        core = CoreSessionContributor(base_domain="example.com")
        flock = RavnFlockContributor(launch_spec_provider=template_provider)

        ctx = SessionContext(launch_spec="ravn-flock")
        contributions = [
            await core.contribute(session, ctx),
            await flock.contribute(session, ctx),
        ]

        spec = SessionSpec.merge(contributions)

        # Core values present
        assert "session" in spec.values
        assert spec.values["session"]["name"] == "test-flock"

        # Flock values present
        assert "mesh" in spec.values
        assert spec.values["mesh"]["enabled"] is True

        # Ravn containers in pod spec
        assert len(spec.pod_spec.extra_containers) == 2

        # Mimir volume present
        volume_names = [v["name"] for v in spec.pod_spec.volumes]
        assert "mimir-local" not in volume_names

        # Init containers merged
        assert len(spec.pod_spec.init_containers) == 2

    async def test_merge_preserves_skuld_env(self, session, flock_template):
        template_provider = MagicMock()
        template_provider.get.return_value = flock_template

        core = CoreSessionContributor(base_domain="example.com")
        flock = RavnFlockContributor(launch_spec_provider=template_provider)

        ctx = SessionContext(launch_spec="ravn-flock")
        contributions = [
            await core.contribute(session, ctx),
            await flock.contribute(session, ctx),
        ]
        spec = SessionSpec.merge(contributions)

        env_names = {e["name"] for e in spec.pod_spec.env}
        assert "SKULD__MESH__ENABLED" in env_names
        assert "SKULD__MESH__PEER_ID" in env_names

    async def test_non_flock_session_no_ravn_containers(self, session, session_template):
        template_provider = MagicMock()
        template_provider.get.return_value = session_template

        core = CoreSessionContributor(base_domain="example.com")
        flock = RavnFlockContributor(launch_spec_provider=template_provider)

        ctx = SessionContext(launch_spec="default")
        contributions = [
            await core.contribute(session, ctx),
            await flock.contribute(session, ctx),
        ]
        spec = SessionSpec.merge(contributions)

        assert spec.pod_spec.extra_containers == ()


# ---------------------------------------------------------------------------
# Profile provider path
# ---------------------------------------------------------------------------


class TestProfileProviderPath:
    async def test_profile_provider_resolves_flock(self, session, flock_profile):
        profile_provider = MagicMock()
        profile_provider.get.return_value = flock_profile
        c = RavnFlockContributor(launch_spec_provider=profile_provider)
        ctx = SessionContext(launch_spec="ravn-flock")
        result = await c.contribute(session, ctx)

        assert result.pod_spec is not None
        assert len(result.pod_spec.extra_containers) == 2

    async def test_default_profile_fallback(self, session, flock_profile):
        profile_provider = MagicMock()
        profile_provider.get.return_value = None
        profile_provider.get_default.return_value = flock_profile
        c = RavnFlockContributor(launch_spec_provider=profile_provider)
        ctx = SessionContext(launch_spec="nonexistent")
        result = await c.contribute(session, ctx)

        assert result.pod_spec is not None
        assert len(result.pod_spec.extra_containers) == 2

    async def test_template_takes_precedence_over_profile(
        self, session, flock_template, session_template
    ):
        template_provider = MagicMock()
        template_provider.get.return_value = session_template
        profile_provider = MagicMock()
        profile_provider.get.return_value = MagicMock(workload_type="ravn_flock")

        c = RavnFlockContributor(launch_spec_provider=template_provider)
        ctx = SessionContext(launch_spec="default")
        result = await c.contribute(session, ctx)

        # Template has workload_type='session' — should no-op
        assert result.values == {}
        assert result.pod_spec is None


# ---------------------------------------------------------------------------
# Extra kwargs ignored
# ---------------------------------------------------------------------------


class TestExtraKwargs:
    def test_extra_kwargs_ignored(self):
        c = RavnFlockContributor(
            launch_spec_provider=None,
            storage=None,
            gateway=None,
            unknown_kwarg="ignored",
        )
        assert c.name == "ravn_flock"


# ---------------------------------------------------------------------------
# LLM config passthrough
# ---------------------------------------------------------------------------

_LLM_CONFIG = {
    "model": "Qwen/Qwen3-Coder-30B-A3B-Instruct",
    "max_tokens": 8192,
    "timeout": 300.0,
    "provider": {
        "adapter": "ravn.adapters.llm.openai.OpenAICompatibleAdapter",
        "kwargs": {
            "base_url": "https://vllm.valaskjalf.asgard.niuu.world",
            "api_key": "",
        },
    },
}


@pytest.fixture
def flock_template_with_llm():
    return LaunchSpec(
        name="ravn-flock-llm",
        workload_type="ravn_flock",
        workload_config={
            "personas": ["coordinator", "reviewer"],
            "mesh": {"transport": "nng"},
            "mimir": {},
            "sleipnir": {},
            "llm_config": _LLM_CONFIG,
        },
    )


class TestLLMConfigPassthrough:
    async def test_llm_block_in_ravn_config_when_provided(self, session, flock_template_with_llm):
        provider = MagicMock()
        provider.get.return_value = flock_template_with_llm
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(launch_spec="ravn-flock-llm")
        result = await c.contribute(session, ctx)

        for persona in ("coordinator", "reviewer"):
            cfg = _extract_mounted_config(result.pod_spec, persona)
            assert "llm:" in cfg
            assert "Qwen/Qwen3-Coder-30B-A3B-Instruct" in cfg
            assert "vllm.valaskjalf.asgard.niuu.world" in cfg

    async def test_no_llm_block_when_not_provided(self, session, flock_template):
        """flock_template has no llm_config — no llm: block emitted."""
        provider = MagicMock()
        provider.get.return_value = flock_template
        c = RavnFlockContributor(launch_spec_provider=provider)
        ctx = SessionContext(launch_spec="ravn-flock")
        result = await c.contribute(session, ctx)

        for persona in ("coordinator", "reviewer"):
            cfg = _extract_mounted_config(result.pod_spec, persona)
            assert "llm:" not in cfg

    async def test_llm_config_from_workload_context(self, session):
        """When workload_type comes directly via SessionContext (SpawnRequest path)."""
        c = RavnFlockContributor()
        ctx = SessionContext(
            workload_type="ravn_flock",
            workload_config={
                "personas": ["coordinator"],
                "llm_config": _LLM_CONFIG,
            },
        )
        result = await c.contribute(session, ctx)

        assert result.pod_spec is not None
        cfg = _extract_mounted_config(result.pod_spec, "coordinator")
        assert "llm:" in cfg
        assert "Qwen/Qwen3-Coder-30B-A3B-Instruct" in cfg

    async def test_empty_llm_config_dict_not_emitted(self, session):
        """An empty llm_config dict should not produce an llm: block."""
        c = RavnFlockContributor()
        ctx = SessionContext(
            workload_type="ravn_flock",
            workload_config={
                "personas": ["coordinator"],
                "llm_config": {},
            },
        )
        result = await c.contribute(session, ctx)

        cfg = _extract_mounted_config(result.pod_spec, "coordinator")
        assert "llm:" not in cfg

    async def test_all_nodes_receive_same_llm_config(self, session):
        """All ravn nodes in a flock receive the same llm_config."""
        llm = {"model": "anthropic/claude-sonnet-4-6", "max_tokens": 4096}
        c = RavnFlockContributor()
        ctx = SessionContext(
            workload_type="ravn_flock",
            workload_config={
                "personas": ["coordinator", "reviewer"],
                "llm_config": llm,
            },
        )
        result = await c.contribute(session, ctx)

        for persona in ("coordinator", "reviewer"):
            cfg = _extract_mounted_config(result.pod_spec, persona)
            assert "claude-sonnet-4-6" in cfg


# ---------------------------------------------------------------------------
# _normalize_personas
# ---------------------------------------------------------------------------


class TestNormalizePersonas:
    def test_legacy_list_str(self):
        result = _normalize_personas(["coordinator", "reviewer"])
        assert result == [{"name": "coordinator"}, {"name": "reviewer"}]

    def test_new_list_dict(self):
        raw = [
            {"name": "coordinator"},
            {"name": "reviewer", "llm": {"primary_alias": "powerful"}},
        ]
        result = _normalize_personas(raw)
        assert result == raw

    def test_mixed_str_and_dict(self):
        raw = ["coordinator", {"name": "reviewer", "llm": {"primary_alias": "powerful"}}]
        result = _normalize_personas(raw)
        assert result == [
            {"name": "coordinator"},
            {"name": "reviewer", "llm": {"primary_alias": "powerful"}},
        ]

    def test_dict_without_name_skipped(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = _normalize_personas([{"llm": {"model": "gpt-4"}}])
        assert result == []
        assert "without 'name'" in caplog.text

    def test_non_str_non_dict_skipped(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = _normalize_personas([42])
        assert result == []
        assert "non-str/dict" in caplog.text

    def test_allowed_tools_dropped(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = _normalize_personas([{"name": "reviewer", "allowed_tools": ["bash", "read"]}])
        assert len(result) == 1
        assert "allowed_tools" not in result[0]
        assert "dropping security key" in caplog.text

    def test_forbidden_tools_dropped(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = _normalize_personas([{"name": "reviewer", "forbidden_tools": ["rm"]}])
        assert len(result) == 1
        assert "forbidden_tools" not in result[0]
        assert "dropping security key" in caplog.text

    def test_empty_list(self):
        assert _normalize_personas([]) == []

    def test_preserves_extra_fields(self):
        raw = [
            {
                "name": "reviewer",
                "llm": {"primary_alias": "powerful"},
                "system_prompt_extra": "Be thorough.",
                "iteration_budget": 40,
            }
        ]
        result = _normalize_personas(raw)
        assert result[0]["system_prompt_extra"] == "Be thorough."
        assert result[0]["iteration_budget"] == 40


# ---------------------------------------------------------------------------
# Persona dict format — end-to-end through contribute()
# ---------------------------------------------------------------------------


class TestPersonaDictFormat:
    async def test_legacy_str_format_still_works(self, session):
        """Regression: legacy list[str] personas keep working."""
        c = RavnFlockContributor()
        ctx = SessionContext(
            workload_type="ravn_flock",
            workload_config={
                "personas": ["coordinator", "reviewer"],
            },
        )
        result = await c.contribute(session, ctx)

        assert result.pod_spec is not None
        names = [ctr["name"] for ctr in result.pod_spec.extra_containers]
        assert "ravn-coordinator" in names
        assert "ravn-reviewer" in names

    async def test_new_dict_format_accepted(self, session):
        """New list[dict] personas accepted and produce correct containers."""
        c = RavnFlockContributor()
        ctx = SessionContext(
            workload_type="ravn_flock",
            workload_config={
                "personas": [
                    {"name": "coordinator"},
                    {"name": "reviewer", "llm": {"primary_alias": "powerful"}},
                ],
            },
        )
        result = await c.contribute(session, ctx)

        assert result.pod_spec is not None
        names = [ctr["name"] for ctr in result.pod_spec.extra_containers]
        assert "ravn-coordinator" in names
        assert "ravn-reviewer" in names

    async def test_mixed_format_accepted(self, session):
        """Mixed str+dict personas in the same list are accepted."""
        c = RavnFlockContributor()
        ctx = SessionContext(
            workload_type="ravn_flock",
            workload_config={
                "personas": [
                    "coordinator",
                    {"name": "reviewer", "llm": {"primary_alias": "powerful"}},
                    {"name": "security-auditor"},
                ],
            },
        )
        result = await c.contribute(session, ctx)

        assert result.pod_spec is not None
        assert len(result.pod_spec.extra_containers) == 3
        names = [ctr["name"] for ctr in result.pod_spec.extra_containers]
        assert "ravn-coordinator" in names
        assert "ravn-reviewer" in names
        assert "ravn-security-auditor" in names

    async def test_dict_format_peer_ids_correct(self, session):
        """Peer IDs use the name from the dict, not the dict itself."""
        c = RavnFlockContributor()
        ctx = SessionContext(
            workload_type="ravn_flock",
            workload_config={
                "personas": [
                    {"name": "coordinator"},
                    {"name": "reviewer"},
                ],
            },
        )
        result = await c.contribute(session, ctx)

        for ctr in result.pod_spec.extra_containers:
            env = {e["name"]: e["value"] for e in ctr["env"]}
            peer_id = env["RAVN_PEER_ID"]
            assert peer_id.startswith("flock-")
            assert peer_id in ("flock-coordinator", "flock-reviewer")


# ---------------------------------------------------------------------------
# WorkloadPersonaOverride typed helper
# ---------------------------------------------------------------------------


class TestWorkloadPersonaOverride:
    def test_to_dict_minimal(self):
        override = WorkloadPersonaOverride(name="coordinator")
        d = override.to_dict()
        assert d == {"name": "coordinator"}

    def test_to_dict_with_llm(self):
        override = WorkloadPersonaOverride(
            name="reviewer",
            llm={"primary_alias": "powerful", "thinking_enabled": True},
        )
        d = override.to_dict()
        assert d == {
            "name": "reviewer",
            "llm": {"primary_alias": "powerful", "thinking_enabled": True},
        }

    def test_to_dict_with_all_fields(self):
        override = WorkloadPersonaOverride(
            name="reviewer",
            llm={"primary_alias": "powerful"},
            system_prompt_extra="Be thorough.",
            iteration_budget=40,
        )
        d = override.to_dict()
        assert d == {
            "name": "reviewer",
            "llm": {"primary_alias": "powerful"},
            "system_prompt_extra": "Be thorough.",
            "iteration_budget": 40,
        }

    def test_to_dict_usable_in_workload_config(self, session):
        """WorkloadPersonaOverride.to_dict() produces valid workload_config entries."""
        overrides = [
            WorkloadPersonaOverride(name="coordinator").to_dict(),
            WorkloadPersonaOverride(
                name="reviewer",
                llm={"primary_alias": "powerful"},
            ).to_dict(),
        ]
        result = _normalize_personas(overrides)
        assert len(result) == 2
        assert result[0]["name"] == "coordinator"
        assert result[1]["name"] == "reviewer"
        assert result[1]["llm"] == {"primary_alias": "powerful"}

    def test_frozen(self):
        override = WorkloadPersonaOverride(name="coordinator")
        with pytest.raises(AttributeError):
            override.name = "reviewer"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Persona source wiring (NIU-642)
# ---------------------------------------------------------------------------


_FLOCK_WORKLOAD_CONFIG = {
    "personas": ["coordinator", "reviewer"],
    "mesh": {"transport": "nng"},
    "mimir": {},
    "sleipnir": {},
}


def _make_flock_contributor(**kwargs) -> RavnFlockContributor:  # noqa: ANN001
    """Return a contributor backed by an in-context flock workload_config."""
    return RavnFlockContributor(**kwargs)


async def _contribute_with_mode(session, mode: str, **extra_kwargs) -> tuple:
    """Contribute via direct workload_config injection and return (values, pod_spec)."""
    c = _make_flock_contributor(persona_source_mode=mode, **extra_kwargs)
    ctx = SessionContext(
        workload_type="ravn_flock",
        workload_config=_FLOCK_WORKLOAD_CONFIG,
    )
    result = await c.contribute(session, ctx)
    return result.values, result.pod_spec


class TestPersonaSourceMountedVolume:
    async def test_configmap_volume_added_to_pod_spec(self, session) -> None:
        _, pod_spec = await _contribute_with_mode(
            session,
            "mountedVolume",
            persona_source_configmap_name="ravn-personas",
            persona_source_mount_path="/etc/ravn/personas",
        )
        volume_names = {v["name"] for v in pod_spec.volumes}
        assert "ravn-personas" in volume_names

    async def test_configmap_volume_references_correct_configmap(self, session) -> None:
        _, pod_spec = await _contribute_with_mode(
            session,
            "mountedVolume",
            persona_source_configmap_name="my-custom-personas",
        )
        cm_vols = [v for v in pod_spec.volumes if v.get("name") == "ravn-personas"]
        assert len(cm_vols) == 1
        assert cm_vols[0]["configMap"]["name"] == "my-custom-personas"

    async def test_mount_added_to_every_ravn_sidecar(self, session) -> None:
        mount_path = "/etc/ravn/personas"
        _, pod_spec = await _contribute_with_mode(
            session,
            "mountedVolume",
            persona_source_mount_path=mount_path,
        )
        for container in pod_spec.extra_containers:
            mount_paths = {m["mountPath"] for m in container["volumeMounts"]}
            assert mount_path in mount_paths, (
                f"Container {container['name']!r} missing persona mount"
            )

    async def test_persona_mount_is_readonly(self, session) -> None:
        _, pod_spec = await _contribute_with_mode(session, "mountedVolume")
        for container in pod_spec.extra_containers:
            persona_mounts = [m for m in container["volumeMounts"] if m["name"] == "ravn-personas"]
            assert len(persona_mounts) == 1
            assert persona_mounts[0].get("readOnly") is True

    async def test_ravn_config_includes_mounted_volume_adapter(self, session) -> None:
        mount_path = "/mnt/personas"
        _, pod_spec = await _contribute_with_mode(
            session,
            "mountedVolume",
            persona_source_mount_path=mount_path,
        )
        # Verify the init container YAML config has persona_source pointing to MountedVolume
        config_yaml = _extract_mounted_config(pod_spec, "coordinator")
        assert "MountedVolumePersonaAdapter" in config_yaml
        assert mount_path in config_yaml

    async def test_no_token_env_injected(self, session) -> None:
        _, pod_spec = await _contribute_with_mode(session, "mountedVolume")
        for container in pod_spec.extra_containers:
            env_names = {e["name"] for e in container["env"]}
            assert "RAVN_VOLUNDR_TOKEN" not in env_names


class TestPersonaSourceFilesystem:
    async def test_no_configmap_volume(self, session) -> None:
        _, pod_spec = await _contribute_with_mode(session, "filesystem")
        volume_names = {v["name"] for v in pod_spec.volumes}
        assert "ravn-personas" not in volume_names

    async def test_no_persona_mount_on_sidecars(self, session) -> None:
        _, pod_spec = await _contribute_with_mode(session, "filesystem")
        for container in pod_spec.extra_containers:
            mount_names = {m["name"] for m in container["volumeMounts"]}
            assert "ravn-personas" not in mount_names

    async def test_no_token_env_injected(self, session) -> None:
        _, pod_spec = await _contribute_with_mode(session, "filesystem")
        for container in pod_spec.extra_containers:
            env_names = {e["name"] for e in container["env"]}
            assert "RAVN_VOLUNDR_TOKEN" not in env_names

    async def test_ravn_config_has_no_persona_source(self, session) -> None:
        _, pod_spec = await _contribute_with_mode(session, "filesystem")
        config_yaml = _extract_mounted_config(pod_spec, "coordinator")
        assert "persona_source" not in config_yaml


class TestPersonaSourceHttp:
    async def test_no_configmap_volume(self, session) -> None:
        _, pod_spec = await _contribute_with_mode(
            session,
            "http",
            persona_source_http_base_url="http://volundr:8080",
        )
        volume_names = {v["name"] for v in pod_spec.volumes}
        assert "ravn-personas" not in volume_names

    async def test_workload_identity_env_used_for_http_personas(self, session) -> None:
        _, pod_spec = await _contribute_with_mode(
            session,
            "http",
            persona_source_http_base_url="http://volundr:8080",
        )
        for container in pod_spec.extra_containers:
            env = {e["name"]: e for e in container["env"]}
            assert "RAVN_VOLUNDR_TOKEN" not in env
            assert env["NIUU_WORKLOAD_IDENTITY_TOKEN_FILE"]["value"].endswith("/token")

    async def test_no_legacy_token_env_injected(self, session) -> None:
        _, pod_spec = await _contribute_with_mode(
            session,
            "http",
            persona_source_http_base_url="http://volundr:8080",
        )
        for container in pod_spec.extra_containers:
            env_names = {e["name"] for e in container["env"]}
            assert "RAVN_VOLUNDR_TOKEN" not in env_names

    async def test_ravn_config_includes_http_adapter(self, session) -> None:
        base_url = "http://volundr:8080"
        _, pod_spec = await _contribute_with_mode(
            session,
            "http",
            persona_source_http_base_url=base_url,
        )
        config_yaml = _extract_mounted_config(pod_spec, "coordinator")
        assert "HttpPersonaAdapter" in config_yaml
        assert base_url in config_yaml

    async def test_no_persona_mount_on_sidecars(self, session) -> None:
        _, pod_spec = await _contribute_with_mode(
            session,
            "http",
            persona_source_http_base_url="http://volundr:8080",
        )
        for container in pod_spec.extra_containers:
            mount_names = {m["name"] for m in container["volumeMounts"]}
            assert "ravn-personas" not in mount_names


# ---------------------------------------------------------------------------
# Per-persona LLM overrides — acceptance criteria from NIU-638
# ---------------------------------------------------------------------------


class TestPerPersonaLLMOverrides:
    async def test_two_sidecars_with_different_llm_aliases_produce_distinct_yaml(self, session):
        """reviewer(powerful, thinking=true) + security-auditor(balanced) → distinct YAML."""
        c = RavnFlockContributor()
        ctx = SessionContext(
            workload_type="ravn_flock",
            workload_config={
                "personas": [
                    {
                        "name": "reviewer",
                        "llm": {"primary_alias": "powerful", "thinking_enabled": True},
                    },
                    {
                        "name": "security-auditor",
                        "llm": {"primary_alias": "balanced"},
                    },
                ],
            },
        )
        result = await c.contribute(session, ctx)

        reviewer_cfg = _extract_mounted_config(result.pod_spec, "reviewer")
        auditor_cfg = _extract_mounted_config(result.pod_spec, "security-auditor")

        # Each sidecar has an llm: section
        assert "llm:" in reviewer_cfg
        assert "llm:" in auditor_cfg

        # The two sidecars have distinct LLM aliases
        assert "powerful" in reviewer_cfg
        assert "balanced" in auditor_cfg
        assert "balanced" not in reviewer_cfg
        assert "powerful" not in auditor_cfg

        # thinking_enabled only appears in reviewer
        assert "thinking_enabled: true" in reviewer_cfg
        assert "thinking_enabled" not in auditor_cfg

    async def test_per_persona_llm_overrides_global_llm(self, session):
        """Per-persona LLM alias overrides the global llm_config alias."""
        c = RavnFlockContributor()
        ctx = SessionContext(
            workload_type="ravn_flock",
            workload_config={
                "personas": [
                    {"name": "coordinator"},
                    {
                        "name": "reviewer",
                        "llm": {"primary_alias": "powerful"},
                    },
                ],
                "llm_config": {"primary_alias": "balanced", "max_tokens": 4096},
            },
        )
        result = await c.contribute(session, ctx)

        coordinator_cfg = _extract_mounted_config(result.pod_spec, "coordinator")
        reviewer_cfg = _extract_mounted_config(result.pod_spec, "reviewer")

        # coordinator inherits global alias
        assert "balanced" in coordinator_cfg
        assert "4096" in coordinator_cfg

        # reviewer overrides alias but inherits max_tokens from global
        assert "powerful" in reviewer_cfg
        assert "4096" in reviewer_cfg
        assert "balanced" not in reviewer_cfg

    async def test_system_prompt_extra_embedded_in_sidecar_yaml(self, session):
        """system_prompt_extra is written to persona_overrides block in sidecar YAML."""
        c = RavnFlockContributor()
        ctx = SessionContext(
            workload_type="ravn_flock",
            workload_config={
                "personas": [
                    {
                        "name": "reviewer",
                        "system_prompt_extra": "Be extra thorough about security.",
                    },
                    {"name": "coordinator"},
                ],
            },
        )
        result = await c.contribute(session, ctx)

        reviewer_cfg = _extract_mounted_config(result.pod_spec, "reviewer")
        coordinator_cfg = _extract_mounted_config(result.pod_spec, "coordinator")

        assert "persona_overrides:" in reviewer_cfg
        assert "Be extra thorough about security." in reviewer_cfg
        assert "persona_overrides:" not in coordinator_cfg

    async def test_iteration_budget_embedded_in_initiative_block(self, session):
        """iteration_budget is written to both initiative and persona_overrides blocks."""
        c = RavnFlockContributor()
        ctx = SessionContext(
            workload_type="ravn_flock",
            workload_config={
                "personas": [
                    {"name": "reviewer", "iteration_budget": 40},
                    {"name": "coordinator"},
                ],
            },
        )
        result = await c.contribute(session, ctx)

        reviewer_cfg = _extract_mounted_config(result.pod_spec, "reviewer")
        coordinator_cfg = _extract_mounted_config(result.pod_spec, "coordinator")

        # Must appear in both initiative (future use) and persona_overrides (ravn reads it here)
        assert "iteration_budget: 40" in reviewer_cfg
        reviewer_parsed = yaml.safe_load(reviewer_cfg)
        assert reviewer_parsed["persona_overrides"]["iteration_budget"] == 40
        assert "iteration_budget" not in coordinator_cfg

    async def test_consumes_event_types_embedded_in_persona_overrides(self, session):
        """consumes_event_types is written to persona_overrides for sidecar startup."""
        c = RavnFlockContributor()
        ctx = SessionContext(
            workload_type="ravn_flock",
            workload_config={
                "personas": [
                    {"name": "reviewer", "consumes_event_types": ["review.requested"]},
                    {"name": "run-executor"},
                ],
            },
        )
        result = await c.contribute(session, ctx)

        reviewer_cfg = _extract_mounted_config(result.pod_spec, "reviewer")
        reviewer_parsed = yaml.safe_load(reviewer_cfg)
        assert reviewer_parsed["persona_overrides"]["consumes_event_types"] == ["review.requested"]

    async def test_per_persona_max_concurrent_tasks(self, session):
        """max_concurrent_tasks from persona override replaces global value in initiative."""
        c = RavnFlockContributor()
        ctx = SessionContext(
            workload_type="ravn_flock",
            workload_config={
                "personas": [
                    {"name": "reviewer", "max_concurrent_tasks": 1},
                    {"name": "coordinator"},
                ],
                "max_concurrent_tasks": 5,
            },
        )
        result = await c.contribute(session, ctx)

        reviewer_cfg = _extract_mounted_config(result.pod_spec, "reviewer")
        coordinator_cfg = _extract_mounted_config(result.pod_spec, "coordinator")

        import yaml as _yaml

        reviewer_parsed = _yaml.safe_load(reviewer_cfg)
        coordinator_parsed = _yaml.safe_load(coordinator_cfg)

        assert reviewer_parsed["initiative"]["max_concurrent_tasks"] == 1
        assert coordinator_parsed["initiative"]["max_concurrent_tasks"] == 5

    async def test_daily_budget_is_written_to_ravn_configs(self, session):
        """daily_budget_usd from workload_config becomes ravn budget.daily_cap_usd."""
        c = RavnFlockContributor()
        ctx = SessionContext(
            workload_type="ravn_flock",
            workload_config={
                "personas": [{"name": "reviewer"}, {"name": "coordinator"}],
                "daily_budget_usd": 25.0,
            },
        )
        result = await c.contribute(session, ctx)

        reviewer_cfg = _extract_mounted_config(result.pod_spec, "reviewer")
        coordinator_cfg = _extract_mounted_config(result.pod_spec, "coordinator")

        import yaml as _yaml

        reviewer_parsed = _yaml.safe_load(reviewer_cfg)
        coordinator_parsed = _yaml.safe_load(coordinator_cfg)

        assert reviewer_parsed["budget"]["daily_cap_usd"] == 25.0
        assert coordinator_parsed["budget"]["daily_cap_usd"] == 25.0
        assert result.values["flock"]["daily_budget_usd"] == 25.0

    async def test_no_persona_overrides_block_when_no_extra(self, session):
        """No persona_overrides block emitted when system_prompt_extra is absent."""
        c = RavnFlockContributor()
        ctx = SessionContext(
            workload_type="ravn_flock",
            workload_config={"personas": ["coordinator", "reviewer"]},
        )
        result = await c.contribute(session, ctx)

        for persona in ("coordinator", "reviewer"):
            cfg = _extract_mounted_config(result.pod_spec, persona)
            assert "persona_overrides:" not in cfg

    async def test_merge_precedence_persona_over_global(self, session):
        """Merge precedence: persona-override > global."""
        c = RavnFlockContributor()
        ctx = SessionContext(
            workload_type="ravn_flock",
            workload_config={
                "personas": [
                    {"name": "reviewer", "llm": {"primary_alias": "powerful"}},
                ],
                "llm_config": {"primary_alias": "balanced"},
            },
        )
        result = await c.contribute(session, ctx)

        cfg = _extract_mounted_config(result.pod_spec, "reviewer")
        assert "powerful" in cfg
        assert "balanced" not in cfg

    async def test_allowed_tools_in_persona_override_stripped(self, session, caplog):
        """allowed_tools in persona dict is stripped with a WARN (security boundary)."""
        with caplog.at_level(logging.WARNING):
            c = RavnFlockContributor()
            ctx = SessionContext(
                workload_type="ravn_flock",
                workload_config={
                    "personas": [
                        {"name": "reviewer", "allowed_tools": ["bash", "read"]},
                    ],
                },
            )
            result = await c.contribute(session, ctx)

        assert result.pod_spec is not None
        assert "dropping security key" in caplog.text
        cfg = _extract_mounted_config(result.pod_spec, "reviewer")
        assert "allowed_tools" not in cfg


class TestMimirHelpers:
    def test_split_edge_label_and_string_list_helpers(self):
        assert _split_workflow_edge_label("code.requested -> code.changed") == (
            "code.requested",
            "code.changed",
        )
        assert _split_workflow_edge_label(None) == ("", "")
        assert _string_list([" a ", "", None, "b"]) == ["a", "None", "b"]
        assert _string_list("bad") == []

    def test_normalize_instance_and_workload_config_helpers(self):
        assert _normalize_instance({"name": "shared", "path": "/tmp/shared"}) == {
            "name": "shared",
            "role": "shared",
            "path": "/tmp/shared",
        }
        assert _normalize_instance({"name": "shared", "url": "https://mimir.example"}) == {
            "name": "shared",
            "role": "shared",
            "url": "https://mimir.example",
        }
        assert _normalize_instance({"name": "shared"}) is None
        assert _normalize_instance({"url": "https://mimir.example"}) is None

        assert _normalize_mimir_workload_config({}, "https://legacy.example") == {
            "hosted_url": "https://legacy.example"
        }
        assert _normalize_mimir_workload_config(
            {"hosted_url": "https://explicit.example"},
            "https://legacy.example",
        ) == {"hosted_url": "https://explicit.example"}

    def test_resolve_mimir_runtime_supports_hosted_registry_ephemeral_and_explicit_routing(self):
        instances, routing = _resolve_mimir_runtime(
            {
                "hosted_url": "https://hosted.example",
                "instances": [
                    {"name": "shared", "url": "https://shared.example", "categories": ["entity"]},
                    {"name": "shared", "url": "https://duplicate.example"},
                    {"url": "https://invalid.example"},
                    "bad",
                ],
                "registry_refs": [
                    {
                        "mount_name": "registry-a",
                        "path": "/mnt/registry-a",
                        "url": "https://registry-a.example",
                        "role": "shared",
                        "categories": ["directive"],
                    },
                    {
                        "registryEntryId": "registry-b",
                        "url": "https://registry-b.example",
                    },
                    {
                        "mount_name": "hosted-backed",
                        "role": "shared",
                    },
                    {"mount_name": "registry-a", "url": "https://duplicate.example"},
                    "bad",
                ],
                "ephemeral_locals": [
                    {"mount_name": "scratch", "categories": ["draft"]},
                    {"mountName": "scratch-2"},
                    {"mount_name": "scratch"},
                    "bad",
                ],
                "bindings": [
                    {
                        "mount_name": "registry-a",
                        "access": "read_write",
                        "write_prefixes": ["docs/"],
                    },
                    {"mountName": "scratch", "access": "write", "writePrefixes": ["drafts/"]},
                    {"mount_name": "missing", "access": "write", "write_prefixes": ["ignored/"]},
                    "bad",
                ],
                "write_routing": {
                    "rules": [
                        {"prefix": "reviews/", "mounts": ["registry-b", "missing"]},
                        {"prefix": "ignored/", "mounts": []},
                        "bad",
                    ],
                    "default": ["registry-b", "missing"],
                },
                "default_mounts": ["scratch", "missing"],
            }
        )

        assert [instance["name"] for instance in instances] == [
            "shared",
            "registry-a",
            "registry-b",
            "hosted-backed",
            "scratch",
            "scratch-2",
        ]
        assert {"prefix": "self/", "mounts": ["scratch"]} in routing["rules"]
        assert {"prefix": "docs/", "mounts": ["registry-a"]} in routing["rules"]
        assert {"prefix": "drafts/", "mounts": ["scratch"]} in routing["rules"]
        assert {"prefix": "reviews/", "mounts": ["registry-b"]} in routing["rules"]
        assert routing["default"] == ["scratch"]
        registry_a = next(instance for instance in instances if instance["name"] == "registry-a")
        assert registry_a["url"] == "https://registry-a.example"
        assert "path" not in registry_a

    def test_resolve_mimir_runtime_adds_default_hosted_instance_when_no_registry_refs(self):
        instances, routing = _resolve_mimir_runtime({"hosted_url": "https://hosted.example"})

        assert [instance["name"] for instance in instances] == ["hosted"]
        assert {"prefix": "project/", "mounts": ["hosted"]} in routing["rules"]
        assert {"prefix": "entity/", "mounts": ["hosted"]} in routing["rules"]
        assert routing["default"] == ["hosted"]
