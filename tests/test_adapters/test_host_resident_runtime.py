"""Contract tests for the host-process resident runtime adapter.

The adapter's platform entrypoint is replaced by a small script that plays
Skuld (an HTTP server on ``SKULD__PORT``) and Ravn (a sleeper), so the real
spawn, readiness, output recording and termination paths run without the
platform packages starting up.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest
import yaml

from volundr.adapters.outbound import host_resident_runtime as host_runtime
from volundr.adapters.outbound.host_resident_runtime import (
    HostProcessResidentRuntimeController,
    _HostLayout,
)
from volundr.adapters.outbound.resident_container_spec import (
    RESIDENT_RAVN_CONFIG,
    RESIDENT_SKULD_CONFIG,
    ResidentContainerProcess,
)
from volundr.domain.models import (
    ResidentBackend,
    ResidentCapability,
    ResidentDeploymentProfile,
    ResidentDesiredState,
    ResidentEngine,
    ResidentObservedState,
    ResidentRuntime,
)

FAKE_ENTRYPOINT = textwrap.dedent(
    """
    import http.server
    import os
    import signal
    import subprocess
    import sys
    import time

    role = sys.argv[1]
    if os.environ.get("FAKE_IGNORE_TERM"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if role == "ravn" and os.environ.get("FAKE_ESCAPED_CHILD"):
        # A descendant that leaves the process group but keeps the output pipe.
        escaped = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
        )
        print("ravn escaped", escaped.pid, flush=True)
    shown = ("HOME", "SKULD__HOST", "NIUU_CONFIG", "CODEX_HOME", "RAVN_STATE_DIR",
             "RAVN__GATEWAY__PLATFORM__PAT_TOKEN", "RAVN_API_AUTH__ADAPTER", "EXTRA_INHERITED")
    print(role, "argv", " ".join(sys.argv[2:]), flush=True)
    for name in shown:
        print(role, "env", name, os.environ.get(name, "<unset>"), flush=True)
    if role == "skuld":
        if os.environ.get("FAKE_SKULD_EXIT"):
            print("error: skuld refused to start", flush=True)
            sys.exit(3)
        if os.environ.get("FAKE_SKULD_HANG"):
            while True:
                time.sleep(1)

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                return

        server = http.server.HTTPServer(
            (os.environ["SKULD__HOST"], int(os.environ["SKULD__PORT"])), Handler
        )
        print("skuld listening", flush=True)
        server.serve_forever()
    while True:
        time.sleep(1)
    """
)


class _SkuldRegistry:
    def __init__(self) -> None:
        self.ports: dict[str, int] = {}

    def register(self, session_id: str, port: int) -> None:
        self.ports[session_id] = port

    def unregister(self, session_id: str) -> None:
        self.ports.pop(session_id, None)


def _profile(**values: object) -> ResidentDeploymentProfile:
    return ResidentDeploymentProfile(
        id="ravn-local",
        display_name="Resident Ravn (Local)",
        backend=ResidentBackend.LOCAL,
        engine=ResidentEngine.RAVN,
        capabilities=[ResidentCapability.LOGS, ResidentCapability.RUNTIME_SUSPEND],
        default_model="gpt-5.6-sol",
        deployment={
            "values": {
                "broker": {"cliType": "codex-ws"},
                "resident": {"platform": {"enabled": True, "baseUrl": "http://127.0.0.1:1"}},
                **values,
            }
        },
    )


def _runtime(**updates: object) -> ResidentRuntime:
    runtime = ResidentRuntime(
        owner_id="local-user",
        tenant_id="local",
        name="Host resident",
        persona_name="product-steward",
        model="gpt-5.6-sol",
        backend=ResidentBackend.LOCAL,
        engine=ResidentEngine.RAVN,
        profile_id="ravn-local",
    )
    return runtime.model_copy(update=updates)


@pytest.fixture
def entrypoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, ...]:
    script = tmp_path / "fake_platform.py"
    script.write_text(FAKE_ENTRYPOINT, encoding="utf-8")
    command = (sys.executable, str(script))
    monkeypatch.setattr(host_runtime, "_platform_entrypoint", lambda: command)
    return command


@pytest.fixture
async def controller(tmp_path: Path, entrypoint: tuple[str, ...]):
    home = tmp_path / "operator-home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "auth.json").write_text("{}", encoding="utf-8")
    instance = HostProcessResidentRuntimeController(
        residents_dir=str(tmp_path / "residents"),
        host_home_dir=str(home),
        volundr_api_url="http://127.0.0.1:1",
        ready_timeout=30,
        ready_poll_interval=0.05,
        stop_timeout=5,
    )
    yield instance
    await instance.close()


def _root(tmp_path: Path, runtime: ResidentRuntime) -> Path:
    return tmp_path / "residents" / str(runtime.id) / "sandbox"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def _log_messages(
    controller: HostProcessResidentRuntimeController,
    runtime: ResidentRuntime,
    marker: str,
) -> list[str]:
    for _ in range(100):
        page = await controller.logs(runtime, lines=200, sources=(), min_level="")
        messages = [f"[{entry.source}] {entry.message}" for entry in page.entries]
        if any(marker in message for message in messages):
            return messages
        await asyncio.sleep(0.05)
    raise AssertionError(f"{marker!r} never reached the resident logs")


@pytest.mark.asyncio
async def test_host_resident_lifecycle_runs_without_a_container_engine(
    tmp_path: Path,
    controller: HostProcessResidentRuntimeController,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAVN_API_AUTH__ADAPTER", "platform.only.Adapter")
    registry = _SkuldRegistry()
    controller.set_skuld_registry(registry)
    runtime = _runtime()
    profile = _profile(runtime={"service": {"name": "skuld", "port": 9200}})

    deployed = await controller.deploy(runtime, profile)

    port = deployed.backend_ref["host_port"]
    assert deployed.observed_state is ResidentObservedState.ACTIVE
    assert deployed.backend_ref["kind"] == "HostProcesses"
    assert deployed.backend_ref["process_names"] == ["skuld", "ravn"]
    assert port not in {0, 9200}
    assert deployed.endpoints[0].url == f"/s/{runtime.id}/session"
    assert deployed.conditions[0].reason == "Ready"
    assert registry.ports[str(runtime.id)] == port
    target = controller.resident_proxy_target(
        runtime.model_copy(update={"backend_ref": deployed.backend_ref})
    )
    assert target is not None
    assert (target.connect_host, target.connect_port) == ("127.0.0.1", port)

    root = _root(tmp_path, runtime)
    skuld_config = yaml.safe_load((root / "config" / "skuld.yaml").read_text())
    ravn_config = yaml.safe_load((root / "config" / "ravn.yaml").read_text())
    assert (skuld_config["host"], skuld_config["port"]) == ("127.0.0.1", port)
    assert skuld_config["session"]["workspace_dir"] == str(root / "workspace")
    assert skuld_config["mesh"]["nng"]["pub_sub_address"].startswith("ipc://")
    assert ravn_config["mesh"]["nng"]["req_rep_address"].startswith("ipc://")
    assert ravn_config["permission"]["workspace_root"] == str(root / "workspace")
    assert ravn_config["skuld"]["broker_url"] == f"ws://127.0.0.1:{port}/ws/ravn"
    http = ravn_config["gateway"]["channels"]["http"]
    assert http["host"] == "127.0.0.1"
    assert http["port"] not in {port, 7781}
    assert (root / "home" / ".codex" / "auth.json").read_text() == "{}"

    runtime = runtime.model_copy(update={"backend_ref": deployed.backend_ref})
    messages = await _log_messages(controller, runtime, "skuld listening")
    assert f"[skuld] skuld env HOME {root / 'home'}" in messages
    assert "[skuld] skuld env SKULD__HOST 127.0.0.1" in messages
    assert f"[skuld] skuld env NIUU_CONFIG {root / 'config' / 'skuld.yaml'}" in messages
    assert f"[skuld] skuld env CODEX_HOME {root / 'home' / '.codex'}" in messages
    assert "[skuld] skuld env RAVN_API_AUTH__ADAPTER <unset>" in messages
    ravn_messages = await _log_messages(controller, runtime, "ravn argv daemon")
    assert (
        f"[ravn] ravn argv daemon --config {root / 'config' / 'ravn.yaml'} "
        "--persona product-steward"
    ) in ravn_messages
    assert "[ravn] ravn env RAVN__GATEWAY__PLATFORM__PAT_TOKEN local-mini" in ravn_messages
    assert f"[ravn] ravn env RAVN_STATE_DIR {root / 'workspace' / '.ravn'}" in ravn_messages
    only_ravn = await controller.logs(runtime, lines=5, sources=("ravn",), min_level="")
    assert {entry.source for entry in only_ravn.entries} == {"ravn"}
    assert len(only_ravn.entries) == 5

    reconciled = await controller.reconcile(runtime, profile)
    assert reconciled.backend_ref["pids"] == deployed.backend_ref["pids"]

    suspended = await controller.suspend(runtime)
    assert suspended.observed_state is ResidentObservedState.SUSPENDED
    assert str(runtime.id) not in registry.ports
    assert not any(_alive(pid) for pid in deployed.backend_ref["pids"].values())
    assert not (tmp_path / "residents" / str(runtime.id) / "host-processes.json").exists()

    resumed = await controller.resume(runtime)
    assert resumed.observed_state is ResidentObservedState.ACTIVE
    assert registry.ports[str(runtime.id)] == resumed.backend_ref["host_port"]

    restarted = await controller.restart(runtime, profile)
    assert restarted.observed_state is ResidentObservedState.ACTIVE
    assert restarted.backend_ref["pids"] != resumed.backend_ref["pids"]

    assert await controller.delete(runtime) is True
    assert str(runtime.id) not in registry.ports
    assert not (tmp_path / "residents" / str(runtime.id)).exists()
    assert not any(_alive(pid) for pid in restarted.backend_ref["pids"].values())
    assert await controller.delete(runtime) is False


@pytest.mark.asyncio
async def test_reconcile_redeploys_changed_spec_and_honours_suspension(
    controller: HostProcessResidentRuntimeController,
) -> None:
    runtime = _runtime()
    profile = _profile()

    first = await controller.reconcile(runtime, profile)
    assert first.observed_state is ResidentObservedState.ACTIVE

    changed = await controller.reconcile(
        runtime.model_copy(update={"model": "gpt-5.6-terra"}), profile
    )
    assert changed.observed_state is ResidentObservedState.ACTIVE
    assert changed.backend_ref["pids"] != first.backend_ref["pids"]

    suspended = await controller.reconcile(
        runtime.model_copy(update={"desired_state": ResidentDesiredState.SUSPENDED}),
        profile,
    )
    assert suspended.observed_state is ResidentObservedState.SUSPENDED
    assert suspended.endpoints == []
    assert not any(_alive(pid) for pid in changed.backend_ref["pids"].values())


@pytest.mark.asyncio
async def test_resume_before_any_reconcile_waits_for_the_profile(
    controller: HostProcessResidentRuntimeController,
) -> None:
    observation = await controller.resume(_runtime())

    assert observation.observed_state is ResidentObservedState.DEPLOYING
    assert observation.conditions[0].reason == "AwaitingReconcile"
    assert observation.backend_ref["kind"] == "HostProcesses"


@pytest.mark.asyncio
async def test_process_exit_before_readiness_fails_loudly_with_its_output(
    tmp_path: Path,
    controller: HostProcessResidentRuntimeController,
) -> None:
    runtime = _runtime()

    with pytest.raises(RuntimeError, match="skuld exited with code 3") as raised:
        await controller.deploy(runtime, _profile(env={"FAKE_SKULD_EXIT": "1"}))

    assert "skuld refused to start" in str(raised.value)
    assert runtime.id not in controller._residents
    assert not (tmp_path / "residents" / str(runtime.id) / "host-processes.json").exists()


@pytest.mark.asyncio
async def test_readiness_timeout_stops_the_processes(
    tmp_path: Path,
    controller: HostProcessResidentRuntimeController,
) -> None:
    controller._ready_timeout = 0.3
    runtime = _runtime()

    with pytest.raises(TimeoutError, match="not ready within"):
        await controller.deploy(runtime, _profile(env={"FAKE_SKULD_HANG": "1"}))

    assert runtime.id not in controller._residents
    recorded = tmp_path / "residents" / str(runtime.id) / "host-processes.json"
    assert not recorded.exists()


@pytest.mark.asyncio
async def test_crashed_resident_stays_failed_until_redeployed(
    controller: HostProcessResidentRuntimeController,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _SkuldRegistry()
    controller.set_skuld_registry(registry)
    runtime = _runtime()
    profile = _profile()
    deployed = await controller.deploy(runtime, profile)
    skuld_pid = deployed.backend_ref["pids"]["skuld"]

    os.killpg(skuld_pid, signal.SIGKILL)
    resident = controller._residents[runtime.id]
    await resident.processes[0].process.wait()

    failed = await controller.reconcile(runtime, profile)
    assert failed.observed_state is ResidentObservedState.FAILED
    assert failed.conditions[0].reason == "ProcessExited"
    assert failed.conditions[0].message == "skuld exited with code -9"
    assert failed.backend_ref["host_port"] == 0
    assert str(runtime.id) not in registry.ports
    assert not _alive(deployed.backend_ref["pids"]["ravn"])

    signalled: list[int] = []
    signal_group = host_runtime._signal_group
    monkeypatch.setattr(host_runtime, "_signal_group", lambda pgid, _sig: signalled.append(pgid))
    still_failed = await controller.reconcile(runtime, profile)
    assert still_failed.observed_state is ResidentObservedState.FAILED
    await controller._stop(runtime)
    # Their groups were already reaped; the ids may belong to someone else now.
    assert signalled == []
    monkeypatch.setattr(host_runtime, "_signal_group", signal_group)

    redeployed = await controller.deploy(runtime, profile)
    assert redeployed.observed_state is ResidentObservedState.ACTIVE
    assert registry.ports[str(runtime.id)] == redeployed.backend_ref["host_port"]


@pytest.mark.asyncio
async def test_orphans_from_a_previous_platform_run_are_stopped_before_start(
    tmp_path: Path,
    controller: HostProcessResidentRuntimeController,
) -> None:
    runtime = _runtime()
    orphan_command = [sys.executable, "-c", "import time; time.sleep(60)"]
    orphan = subprocess.Popen(orphan_command, start_new_session=True)
    # launchd/init reaps a real orphan; reap this one so it cannot linger as a zombie.
    threading.Thread(target=orphan.wait, daemon=True).start()
    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60.5)"],
        start_new_session=True,
    )
    record = tmp_path / "residents" / str(runtime.id) / "host-processes.json"
    record.parent.mkdir(parents=True)
    record.write_text(
        json.dumps(
            {
                "processes": [
                    {"name": "skuld", "pid": orphan.pid, "command": orphan_command},
                    # A recycled PID now running something else must survive.
                    {"name": "ravn", "pid": unrelated.pid, "command": orphan_command},
                    {"name": "gone", "pid": 2**22 + 17, "command": orphan_command},
                ]
            }
        ),
        encoding="utf-8",
    )
    try:
        deployed = await controller.deploy(runtime, _profile())

        assert deployed.observed_state is ResidentObservedState.ACTIVE
        assert orphan.wait(timeout=5) is not None
        assert unrelated.poll() is None
        recorded = json.loads(record.read_text())
        assert [entry["pid"] for entry in recorded["processes"]] == list(
            deployed.backend_ref["pids"].values()
        )
    finally:
        for process in (orphan, unrelated):
            if process.poll() is None:
                process.kill()
                process.wait()


@pytest.mark.asyncio
async def test_unreadable_process_record_fails_loudly(
    tmp_path: Path,
    controller: HostProcessResidentRuntimeController,
) -> None:
    runtime = _runtime()
    record = tmp_path / "residents" / str(runtime.id) / "host-processes.json"
    record.parent.mkdir(parents=True)
    record.write_text("{not json", encoding="utf-8")

    with pytest.raises(RuntimeError, match="unreadable"):
        await controller.deploy(runtime, _profile())


@pytest.mark.asyncio
async def test_close_stops_every_resident(
    controller: HostProcessResidentRuntimeController,
) -> None:
    deployed = await controller.deploy(_runtime(), _profile())

    await controller.close()

    assert controller._residents == {}
    assert not any(_alive(pid) for pid in deployed.backend_ref["pids"].values())


@pytest.mark.asyncio
async def test_inherited_environment_is_an_explicit_allowlist(
    tmp_path: Path,
    entrypoint: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EXTRA_INHERITED", "kept")
    controller = HostProcessResidentRuntimeController(
        residents_dir=str(tmp_path / "residents"),
        mount_agent_credentials=False,
        ready_poll_interval=0.05,
        stop_timeout=5,
        inherited_environment=["PATH", "EXTRA_INHERITED"],
    )
    runtime = _runtime()
    try:
        deployed = await controller.deploy(runtime, _profile())
        runtime = runtime.model_copy(update={"backend_ref": deployed.backend_ref})
        messages = await _log_messages(controller, runtime, "skuld listening")
        assert "[skuld] skuld env EXTRA_INHERITED kept" in messages
        assert not (_root(tmp_path, runtime) / "home" / ".codex" / "auth.json").exists()
    finally:
        await controller.close()


@pytest.mark.asyncio
async def test_supports_only_the_built_in_local_ravn_resident(
    controller: HostProcessResidentRuntimeController,
) -> None:
    profile = _profile()

    assert controller.backend is ResidentBackend.LOCAL
    assert controller.supports(profile) is True
    assert (
        controller.supports(profile.model_copy(update={"backend": ResidentBackend.OPENSHELL}))
        is False
    )
    assert (
        controller.supports(profile.model_copy(update={"engine": ResidentEngine.OPENCLAW})) is False
    )
    assert controller.supports(profile.model_copy(update={"deployment": {}})) is False
    assert controller.supports(_profile(runtime={"processMode": "replace"})) is False
    assert (
        controller.supports(_profile(runtime={"processes": [{"name": "x", "command": ["x"]}]}))
        is False
    )
    assert controller.supports(_profile(runtime={"processes": ["invalid"]})) is False
    assert (
        controller.supports(profile.model_copy(update={"capabilities": [ResidentCapability.FLOCK]}))
        is False
    )


@pytest.mark.asyncio
async def test_unsupported_profiles_are_refused_with_a_remedy(
    controller: HostProcessResidentRuntimeController,
) -> None:
    with pytest.raises(RuntimeError, match="Configure a container resident runtime"):
        await controller.deploy(_runtime(), _profile(runtime={"processMode": "replace"}))


def test_layout_refuses_settings_it_cannot_place_on_the_host(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    layout = _HostLayout(root=root, ravn_http_port=4000)

    assert layout.path("/sandbox") == str(root / "home")
    assert layout.path("/sandbox/workspace") == str(root / "workspace")
    assert layout.translate(["/sandbox/.claude/x", {"k": 1}, "http://h/sandbox"]) == [
        str(root / "home" / ".claude" / "x"),
        {"k": 1},
        "http://h/sandbox",
    ]
    with pytest.raises(RuntimeError, match="not backed by durable local storage"):
        layout.translate("/sandbox/other/file")
    with pytest.raises(RuntimeError, match="embeds a container path"):
        layout.translate("--config=/sandbox/.volundr/ravn.yaml")
    with pytest.raises(RuntimeError, match="binds every network interface"):
        layout.environment({"LISTEN": "tcp://0.0.0.0:7000"})
    with pytest.raises(RuntimeError, match="cannot place resident file"):
        layout.config_file("/sandbox/workspace/other.yaml", b"a: 1\n")
    with pytest.raises(RuntimeError, match="not a configuration mapping"):
        layout.config_file(RESIDENT_SKULD_CONFIG, b"- item\n")
    with pytest.raises(RuntimeError, match="binds every network interface"):
        layout.config_file(RESIDENT_RAVN_CONFIG, b"gateway:\n  channels: {}\nother: 0.0.0.0\n")

    ravn = yaml.safe_load(layout.config_file(RESIDENT_RAVN_CONFIG, b"persona: steward\n"))
    assert ravn == {"persona": "steward"}

    process = ResidentContainerProcess(
        name="skuld",
        command=("skuld",),
        env={},
        files={},
        log_path="/sandbox/.volundr/skuld.log",
    )
    with pytest.raises(RuntimeError, match="built-in Skuld and Ravn"):
        layout.processes((process,), _runtime(), ("python", "-m"), access_token="token")


def test_platform_entrypoint_resolves_source_and_compiled_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert host_runtime._platform_entrypoint() == (sys.executable, "-m")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert host_runtime._platform_entrypoint() == (sys.executable, "platform")

    monkeypatch.delattr(sys, "frozen")
    monkeypatch.setattr(
        host_runtime.importlib.util,
        "find_spec",
        lambda name: None if name == "ravn" else object(),
    )
    with pytest.raises(RuntimeError, match="need the ravn package"):
        host_runtime._platform_entrypoint()


def test_service_port_override_follows_the_runtime_section() -> None:
    assert host_runtime._with_service_port({}, 5) == {"runtime": {"service": {"port": 5}}}
    assert host_runtime._with_service_port(
        {"openshell": {"service": {"name": "skuld", "port": 9200}}},
        5,
    ) == {"openshell": {"service": {"name": "skuld", "port": 5}}}
    values = {"runtime": {"processMode": "append"}, "openshell": {"service": {"port": 1}}}
    assert host_runtime._with_service_port(values, 5)["runtime"] == {
        "processMode": "append",
        "service": {"port": 5},
    }
    assert values["runtime"] == {"processMode": "append"}


def test_reserved_ports_are_distinct() -> None:
    ports = host_runtime._reserve_loopback_ports(3)

    assert len(set(ports)) == 3
    assert all(port > 0 for port in ports)


def test_log_tail_reads_only_the_end_of_large_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(host_runtime, "LOG_CHUNK_BYTES", 16)
    log = tmp_path / "skuld.log"
    log.write_text("".join(f"line {index}\n" for index in range(50)), encoding="utf-8")

    assert host_runtime._tail_text(log, 3) == "line 47\nline 48\nline 49"
    assert host_runtime._tail_text(log, 0) == ""
    assert host_runtime._tail_text(tmp_path / "missing.log", 3) == ""
    assert host_runtime._tail_logs([log, tmp_path / "missing.log"], 1) == "line 49"


@pytest.mark.asyncio
async def test_output_recording_stamps_lines_and_bounds_unterminated_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(host_runtime, "LOG_CHUNK_BYTES", 8)
    stream = asyncio.StreamReader()
    stream.feed_data(b"ready\r\n0123456789abcdef")
    stream.feed_data(b"\npartial")
    stream.feed_eof()
    log = tmp_path / "ravn.log"

    await host_runtime._record_output(stream, log, "ravn")

    lines = log.read_text(encoding="utf-8").splitlines()
    assert [line.split(" ", 1)[1] for line in lines] == [
        "[ravn] ready",
        "[ravn] 012345678",
        "[ravn] 9abcdef",
        "[ravn] partial",
    ]


@pytest.mark.asyncio
async def test_failed_output_recording_is_reported_as_a_failure() -> None:
    async def broken() -> None:
        raise OSError("disk full")

    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(30)"
    )
    output = asyncio.create_task(broken())
    await asyncio.wait([output])
    resident = host_runtime._HostResident(
        spec_hash="hash",
        service_name="skuld",
        service_port=1,
        processes=[
            host_runtime._HostProcess(
                name="skuld",
                command=("skuld",),
                log_path=Path("unused.log"),
                process=process,
                output=output,
            )
        ],
    )
    try:
        assert host_runtime._failure(resident) == "recording skuld output failed: disk full"
    finally:
        process.kill()
        await process.wait()


@pytest.mark.asyncio
async def test_proxy_target_requires_a_running_service(
    controller: HostProcessResidentRuntimeController,
) -> None:
    assert controller.resident_proxy_target(_runtime()) is None


@pytest.mark.asyncio
async def test_redeploying_a_healthy_resident_keeps_its_processes(
    tmp_path: Path,
    entrypoint: tuple[str, ...],
) -> None:
    controller = HostProcessResidentRuntimeController(
        residents_dir=str(tmp_path / "residents"),
        mount_agent_credentials=False,
        ready_poll_interval=0.05,
        stop_timeout=5,
        retain_data_on_delete=True,
    )
    runtime = _runtime()
    try:
        first = await controller.deploy(runtime, _profile())
        second = await controller.deploy(runtime, _profile())
        assert second.backend_ref["pids"] == first.backend_ref["pids"]

        assert await controller.delete(runtime) is True
        assert (_root(tmp_path, runtime) / "config" / "ravn.yaml").is_file()
    finally:
        await controller.close()


@pytest.mark.asyncio
async def test_a_failed_spawn_stops_the_processes_already_started(
    controller: HostProcessResidentRuntimeController,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[host_runtime._HostProcess] = []
    spawn = controller._spawn

    async def spawn_once(*args, **kwargs):
        if started:
            raise OSError("exec format error")
        started.append(await spawn(*args, **kwargs))
        return started[0]

    monkeypatch.setattr(controller, "_spawn", spawn_once)

    with pytest.raises(OSError, match="exec format error"):
        await controller.deploy(_runtime(), _profile())

    assert started[0].process.returncode is not None
    assert controller._residents == {}


@pytest.mark.asyncio
async def test_processes_ignoring_sigterm_are_killed(
    controller: HostProcessResidentRuntimeController,
    caplog: pytest.LogCaptureFixture,
) -> None:
    controller._stop_timeout = 0.2
    runtime = _runtime()
    deployed = await controller.deploy(runtime, _profile(env={"FAKE_IGNORE_TERM": "1"}))

    suspended = await controller.suspend(runtime)

    assert suspended.observed_state is ResidentObservedState.SUSPENDED
    assert not any(_alive(pid) for pid in deployed.backend_ref["pids"].values())
    assert "ignored SIGTERM" in caplog.text


@pytest.mark.asyncio
async def test_stop_does_not_wait_forever_on_an_escaped_descendant(
    controller: HostProcessResidentRuntimeController,
    caplog: pytest.LogCaptureFixture,
) -> None:
    controller._stop_timeout = 0.2
    runtime = _runtime()
    deployed = await controller.deploy(runtime, _profile(env={"FAKE_ESCAPED_CHILD": "1"}))
    runtime = runtime.model_copy(update={"backend_ref": deployed.backend_ref})
    messages = await _log_messages(controller, runtime, "ravn escaped")
    escaped = int(next(m for m in messages if "ravn escaped" in m).rsplit(" ", 1)[1])
    try:
        await controller.suspend(runtime)

        assert "left its process group" in caplog.text
        assert _alive(escaped)
    finally:
        os.kill(escaped, signal.SIGKILL)


def test_a_process_owned_by_another_user_counts_as_alive() -> None:
    assert host_runtime._process_alive(1) is True
