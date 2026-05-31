from __future__ import annotations

import asyncio
import plistlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import ravn.warden as warden_module
from ravn.api.warden_stream import WardenStreamBroker
from ravn.ports.warden_deployer import WardenObservationResult
from ravn.warden.artifacts import (
    daemon_program_arguments,
    local_daemon_program_arguments,
    local_python_executable,
    render_launchd_plist,
    render_systemd_unit,
    runtime_config_payload,
)
from ravn.warden.deployment import (
    build_warden_store,
    create_warden_deployer,
    resolve_deployment_adapter,
)
from ravn.warden.models import WardenObservation, WardenSpec
from ravn.warden.store import WardenStore, _default_wardens_dir, _slugify


class _ObserveOnlyDeployer:
    def __init__(self, status: str) -> None:
        self._status = status

    def install(self, spec: WardenSpec, *, warden_dir: Path, workspace_root: Path | None = None):
        raise AssertionError("not used")

    def start(self, spec: WardenSpec, *, warden_dir: Path):
        raise AssertionError("not used")

    def stop(self, spec: WardenSpec, *, warden_dir: Path):
        raise AssertionError("not used")

    def uninstall(self, spec: WardenSpec, *, warden_dir: Path):
        raise AssertionError("not used")

    def observe(self, spec: WardenSpec, *, warden_dir: Path):
        del warden_dir
        return WardenObservationResult(
            supervisor=spec.supervisor.model_copy(
                update={
                    "observation": WardenObservation(
                        status=self._status,
                        detail=f"{self._status} observed",
                        source="test",
                    )
                }
            )
        )


def test_local_python_executable_falls_back_when_sys_executable_is_empty() -> None:
    with patch("sys.executable", ""):
        assert local_python_executable() == "/usr/bin/python3"


def test_runtime_config_payload_defaults_write_mount_and_enables_secondary_roles(
    tmp_path: Path,
) -> None:
    warden_dir = tmp_path / "warden-home"
    spec = WardenSpec(
        id="council-warden",
        name="Council Warden",
        persona="mimir-warden",
        profile="deep-research",
        mimir={"mount_names": ["scratch", "shared"]},
    )

    payload = runtime_config_payload(spec, workspace_root=tmp_path, warden_dir=warden_dir)

    assert payload["permission"]["workspace_root"] == str(tmp_path.resolve())
    assert payload["logging"] == {"level": "info", "format": "text"}
    assert payload["mimir"]["write_routing"]["default"] == ["scratch"]
    assert payload["mimir"]["instances"] == [
        {
            "name": "scratch",
            "role": "local",
            "path": "~/.ravn/mimir/scratch",
            "read_priority": 20,
        },
        {
            "name": "shared",
            "role": "shared",
            "path": "~/.ravn/mimir/shared",
            "read_priority": 19,
        },
    ]
    assert payload["initiative"]["default_persona"] == "mimir-warden"
    assert payload["initiative"]["queue_journal_path"] == str(warden_dir / "queue.json")
    assert payload["llm"] == {"model": "claude-sonnet-4-6"}
    assert payload["dream_cycle"]["persona"] == "mimir-warden"
    assert payload["dream_cycle"]["state_dir"] == str(warden_dir / "state")
    assert payload["mimir"]["staleness_trigger"]["persona"] == "mimir-warden"
    assert payload["mcp_servers"] == [
        {
            "name": "mimir-scratch",
            "transport": "stdio",
            "command": local_python_executable(),
            "args": [
                "-m",
                "mimir",
                "mcp",
                "--path",
                "~/.ravn/mimir/scratch",
                "--name",
                "scratch",
            ],
            "env": {},
            "enabled": True,
        },
        {
            "name": "mimir-shared",
            "transport": "stdio",
            "command": local_python_executable(),
            "args": [
                "-m",
                "mimir",
                "mcp",
                "--path",
                "~/.ravn/mimir/shared",
                "--name",
                "shared",
            ],
            "env": {},
            "enabled": True,
        },
    ]


def test_daemon_program_arguments_include_optional_profile() -> None:
    spec = WardenSpec(id="profiled", name="Profiled", profile="infra-synthesis")

    assert daemon_program_arguments(spec, config_path="/tmp/warden.yaml") == [
        "/usr/bin/env",
        "ravn",
        "daemon",
        "--config",
        "/tmp/warden.yaml",
        "--persona",
        spec.persona,
        "--profile",
        "infra-synthesis",
    ]
    assert local_daemon_program_arguments(
        spec,
        config_path="/tmp/warden.yaml",
        python_executable="/usr/bin/python3",
    ) == [
        "/usr/bin/python3",
        "-m",
        "ravn",
        "daemon",
        "--config",
        "/tmp/warden.yaml",
        "--persona",
        spec.persona,
        "--profile",
        "infra-synthesis",
    ]


def test_local_supervisor_artifacts_use_runtime_model_env_for_claude() -> None:
    spec = WardenSpec(id="env-warden", name="Env Warden", model="claude-sonnet-4-6")

    with patch.dict(
        "os.environ",
        {
            "HOME": "/Users/tester",
            "PATH": "/tmp/bin:/usr/bin",
        },
        clear=False,
    ):
        plist_text = render_launchd_plist(
            spec,
            config_path=Path("/tmp/warden.yaml"),
            working_directory=Path("/tmp"),
            stdout_path=Path("/tmp/stdout.log"),
            stderr_path=Path("/tmp/stderr.log"),
            python_executable="/usr/bin/python3",
        )
        unit_text = render_systemd_unit(
            spec,
            config_path=Path("/tmp/warden.yaml"),
            working_directory=Path("/tmp"),
            python_executable="/usr/bin/python3",
        )

    payload = plistlib.loads(plist_text.encode("utf-8"))
    assert payload["EnvironmentVariables"]["HOME"] == "/Users/tester"
    assert payload["EnvironmentVariables"]["PATH"] == "/tmp/bin:/usr/bin"
    assert payload["EnvironmentVariables"]["RAVN_LLM__MODEL"] == "claude-sonnet-4-6"
    assert payload["EnvironmentVariables"]["SKULD__CLI_TYPE"] == "claude"
    assert payload["EnvironmentVariables"]["SKULD__TRANSPORT"] == "sdk"
    assert payload["EnvironmentVariables"]["SKULD__TRANSPORT_ADAPTER"] == (
        "skuld.transports.sdk.SDKTransport"
    )
    assert "ANTHROPIC_API_KEY" not in payload["EnvironmentVariables"]
    assert "Environment=HOME=/Users/tester" in unit_text
    assert "Environment=PATH=/tmp/bin:/usr/bin" in unit_text
    assert "Environment=RAVN_LLM__MODEL=claude-sonnet-4-6" in unit_text
    assert "Environment=SKULD__CLI_TYPE=claude" in unit_text
    assert "Environment=SKULD__TRANSPORT=sdk" in unit_text
    assert "Environment=SKULD__TRANSPORT_ADAPTER=skuld.transports.sdk.SDKTransport" in unit_text


def test_local_supervisor_artifacts_use_model_runtime_resolution_for_codex() -> None:
    spec = WardenSpec(id="codex-warden", name="Codex Warden", model="gpt-5.5")

    with patch.dict("os.environ", {}, clear=False):
        plist_text = render_launchd_plist(
            spec,
            config_path=Path("/tmp/codex-warden.yaml"),
            working_directory=Path("/tmp"),
            stdout_path=Path("/tmp/codex-stdout.log"),
            stderr_path=Path("/tmp/codex-stderr.log"),
            python_executable="/usr/bin/python3",
        )

    payload = plistlib.loads(plist_text.encode("utf-8"))
    assert payload["EnvironmentVariables"]["RAVN_LLM__MODEL"] == "gpt-5.5"
    assert payload["EnvironmentVariables"]["SKULD__CLI_TYPE"] == "codex-ws"
    assert payload["EnvironmentVariables"]["SKULD__TRANSPORT_ADAPTER"] == (
        "skuld.transports.codex_ws.CodexWebSocketTransport"
    )
    assert "OPENAI_API_KEY" not in payload["EnvironmentVariables"]


def test_warden_stream_broker_unsubscribe_cleans_up_subscribers() -> None:
    broker = WardenStreamBroker()
    warden = WardenSpec(id="research-warden", name="Research Warden")
    queue = broker.subscribe(warden.id)

    broker.unsubscribe(warden.id, queue)
    broker.unsubscribe(warden.id, queue)
    assert broker._subscribers == {}
    asyncio.run(broker.publish("warden.started", warden))


def test_store_helpers_cover_error_paths_and_role_inference(tmp_path: Path) -> None:
    store = WardenStore(root=tmp_path)

    assert store.list() == []
    assert store.get("missing") is None
    assert store.install("missing") is None
    assert store.start("missing") is None
    assert store.stop("missing") is None
    assert store.uninstall("missing") is None
    assert store.observe("missing") is None
    missing_deleted = store.delete("missing")
    assert missing_deleted is False

    broken_yaml = tmp_path / "broken.yaml"
    broken_yaml.write_text(":\n", encoding="utf-8")
    assert WardenStore.load_from_file(broken_yaml) is None

    scalar_yaml = tmp_path / "scalar.yaml"
    scalar_yaml.write_text("- just\n- a-list\n", encoding="utf-8")
    assert WardenStore.load_from_file(scalar_yaml) is None

    invalid_spec = tmp_path / "invalid.yaml"
    invalid_spec.write_text("name: missing-id\n", encoding="utf-8")
    assert WardenStore.load_from_file(invalid_spec) is None

    non_empty = store.create(WardenSpec(id="", name="Research Warden"))
    extra_file = store.warden_dir(non_empty.id) / "leftover.txt"
    extra_file.write_text("keep", encoding="utf-8")
    deleted = store.delete(non_empty.id)
    assert deleted is True
    assert extra_file.exists()

    enriched = store.save(
        WardenSpec(
            id="scribe",
            name="Scribe",
            persona="draft-followup",
            mimir={"mount_names": []},
            features={"source_trigger_enabled": False, "dream_cycle_enabled": False},
        )
    )
    assert enriched.operator.role == "write"
    assert enriched.operator.tools == ["ravn", "mimir"]
    assert enriched.operator.bio == "Scribe runs the draft-followup persona across attached mounts."

    mystery = store.save(WardenSpec(id="mystery", name="Mystery", persona="enigmatic-helper"))
    assert mystery.operator.role == "autonomy"
    assert store.service_label("mystery") == "dev.niuu.ravn.warden.mystery"

    broken_store = WardenStore(root=tmp_path / "broken-root")
    assert broken_store.list() == []
    invalid_dir = broken_store.warden_dir("bad")
    invalid_dir.mkdir(parents=True)
    (invalid_dir / "warden.yaml").write_text("name: missing-id\n", encoding="utf-8")
    assert broken_store.list() == []


def test_store_uses_env_root_and_maps_observation_states(tmp_path: Path) -> None:
    with patch.dict("os.environ", {"RAVN_WARDENS_DIR": str(tmp_path / "wardens-home")}):
        assert _default_wardens_dir() == (tmp_path / "wardens-home")

    assert _slugify("  Research Warden!!!  ") == "research-warden"
    assert _slugify("???") == "warden"

    for status, expected in (
        ("running", "active"),
        ("missing", "offline"),
        ("idle", "idle"),
        ("degraded", "idle"),
    ):
        store = WardenStore(
            root=tmp_path / status,
            deployer_factory=lambda spec, status=status: _ObserveOnlyDeployer(status),
        )
        created = store.create(WardenSpec(id="", name=f"{status} Warden"))
        observed = store.observe(created.id)
        assert observed is not None
        assert observed.runtime.state == expected


@pytest.mark.parametrize(
    ("persona", "expected"),
    [
        ("index-the-docs", "index"),
        ("write-summary", "write"),
        ("report-findings", "report"),
        ("qa-validator", "verify"),
        ("plan-the-work", "plan"),
        ("coord-manager", "coord"),
        ("research-helper", "investigate"),
        ("health-observer", "observe"),
        ("review-helper", "review"),
    ],
)
def test_store_infer_role_heuristics_cover_keyword_branches(
    tmp_path: Path,
    persona: str,
    expected: str,
) -> None:
    store = WardenStore(root=tmp_path)

    created = store.save(WardenSpec(id=persona, name=persona, persona=persona))

    assert created.operator.role == expected


def test_store_requires_deployer_factory_for_lifecycle_operations(tmp_path: Path) -> None:
    store = WardenStore(root=tmp_path)
    created = store.create(
        WardenSpec(
            id="",
            name="Installed Warden",
            supervisor={"installed": True},
        )
    )

    with pytest.raises(
        RuntimeError,
        match="WardenStore requires a deployment adapter factory",
    ):
        store.start(created.id)


def test_warden_module_exports_and_attribute_errors(tmp_path: Path) -> None:
    assert warden_module.resolve_deployment_adapter("systemd").endswith(
        "SystemdUserWardenDeploymentAdapter"
    )
    exported_store = warden_module.__getattr__("WardenStore")
    assert exported_store is WardenStore

    built = warden_module.build_warden_store(root=tmp_path)
    assert isinstance(built, WardenStore)
    assert built.root == tmp_path

    created = warden_module.create_warden_deployer(
        WardenSpec(
            id="wrapper",
            name="Wrapper",
            deployment="custom",
            deployment_adapter="ravn.adapters.deployment.LaunchdWardenDeploymentAdapter",
            deployment_kwargs={"user_id": 999},
        )
    )
    assert created.__class__.__name__ == "LaunchdWardenDeploymentAdapter"

    with pytest.raises(AttributeError, match="has no attribute 'missing'"):
        warden_module.__getattr__("missing")


def test_deployment_module_resolves_and_constructs_adapter() -> None:
    spec = WardenSpec(
        id="warden",
        name="Warden",
        deployment="custom",
        deployment_adapter="ravn.adapters.deployment.LaunchdWardenDeploymentAdapter",
        deployment_kwargs={"user_id": 123},
    )

    assert resolve_deployment_adapter("unknown").endswith("LaunchdWardenDeploymentAdapter")

    adapter = create_warden_deployer(spec)
    assert adapter.__class__.__name__ == "LaunchdWardenDeploymentAdapter"
    assert adapter._user_id == 123  # noqa: SLF001

    store = build_warden_store(root=Path("/tmp/example"))
    assert isinstance(store, WardenStore)
    assert store.root == Path("/tmp/example")


def test_deployment_module_create_warden_deployer_uses_imported_class() -> None:
    fake_module = MagicMock()
    fake_cls = MagicMock(return_value="adapter-instance")
    fake_module.CustomAdapter = fake_cls

    spec = WardenSpec(
        id="warden",
        name="Warden",
        deployment="custom",
        deployment_adapter="fake.module.CustomAdapter",
        deployment_kwargs={"namespace": "ravn"},
    )

    with patch("importlib.import_module", return_value=fake_module) as import_module:
        adapter = create_warden_deployer(spec)

    import_module.assert_called_once_with("fake.module")
    fake_cls.assert_called_once_with(namespace="ravn")
    assert adapter == "adapter-instance"
