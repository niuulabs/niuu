"""No systemd mutation: fail-closed deployment tests use only owned fixtures."""

import copy
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.error import URLError

import pytest

from scripts import forge_api_release as release


@pytest.fixture
def manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    unit_dir = tmp_path / ".config/systemd/user/forge.service.d"
    unit_dir.mkdir(parents=True)
    (tmp_path / "state.json").write_text("{}")
    (tmp_path / "original.conf").write_text("original")
    health = {"revision": "new", "source_sha256": "digest", "dirty": False, "failed_plugins": []}
    candidate = {
        "root": str(tmp_path / "candidate"),
        "health": health,
        "override_content": f"[Service]\nWorkingDirectory={tmp_path}/candidate\n"
        f"ExecStart=\nExecStart={tmp_path}/candidate/.venv/bin/python\n",
    }
    rollback = {
        **candidate,
        "root": str(tmp_path / "rollback"),
        "health": {**health, "revision": "safe-old"},
        "override_content": candidate["override_content"].replace("candidate", "rollback"),
    }
    return {
        "unit": "forge.service",
        "api_url": "https://host.test:8080",
        "expected_health": {**health, "revision": "old"},
        "candidate": candidate,
        "rollback": rollback,
        "override_path": str(unit_dir / "zz-owned.conf"),
        "protected_pids": [],
        "state_file": str(tmp_path / "state.json"),
        "stable_files": [str(tmp_path / "original.conf")],
        "replay_ids": ["archived"],
        "checks": [
            {"name": "TLS", "url": "https://host.test:9501/health", "json_fields": {"status": "ok"}}
        ],
        "command_timeout_seconds": 5,
        "http_timeout_seconds": 1,
        "ready_timeout_seconds": 5,
        "poll_interval_seconds": 0.01,
        "window": {
            "start": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
            "end": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
        },
    }


@pytest.fixture
def runner(manifest):
    runner = release.LocalRelease(manifest)
    before = {
        "unit": {"process": {"pid": 1}, "files": {"original": "sha"}},
        "processes": {"2": {"pid": 2}},
        "live_gateway_state": {},
        "sessions": {},
        "replay": {},
        "checks": [],
        "stable_files": {},
    }
    runner.preflight = MagicMock(return_value=before)
    runner.install = MagicMock()
    runner.restart = MagicMock()
    runner.ready = MagicMock(return_value={"status": "healthy"})
    after = copy.deepcopy(before)
    after["unit"]["process"] = {"pid": 3}
    after["unit"]["files"][manifest["override_path"]] = "owned-override-sha"
    runner.snapshot = MagicMock(return_value=after)
    return runner


@pytest.mark.parametrize(
    "failure",
    ["manifest", "window", "preflight", "process", "health", "replay", "unit", "new_process"],
)
def test_any_failed_guard_prevents_all_later_mutation(runner, failure):
    prepared = release.prepare(runner)
    if failure == "manifest":
        prepared["manifest_sha256"] = "changed"
    elif failure == "window":
        runner.m["window"] = None
        prepared["manifest_sha256"] = release.digest(runner.m)
    elif failure in {"preflight", "health"}:
        runner.preflight.side_effect = release.GuardError(failure)
    else:
        now = copy.deepcopy(prepared["before"])
        if failure == "process":
            now["processes"] = {}
        elif failure == "new_process":
            now["processes"]["new"] = {"pid": 4}
        elif failure == "replay":
            now["replay"] = {"changed": True}
        else:
            now["unit"]["process"] = {"pid": 5}
        runner.preflight.return_value = now
    with pytest.raises(release.GuardError):
        release.apply(runner, prepared, {})
    runner.install.assert_not_called()
    runner.restart.assert_not_called()


def test_success_only_restarts_selected_api_once(runner):
    record = {}
    release.apply(runner, release.prepare(runner), record)
    assert record["passed"]
    runner.install.assert_called_once_with(runner.m["candidate"])
    runner.restart.assert_called_once_with()


@pytest.mark.parametrize("failure", ["install", "restart", "ready", "preservation", "rollback"])
def test_candidate_failure_uses_audited_safe_rollback_and_preserves_failure(runner, failure):
    prepared = release.prepare(runner)
    if failure in {"install", "restart", "ready"}:
        getattr(runner, failure).side_effect = [release.GuardError("candidate failed"), {}]
    else:
        after = runner.snapshot.return_value
        bad = {**after, "processes": {}}
        runner.snapshot.side_effect = [bad, after]
        if failure == "rollback":
            runner.install.side_effect = [None, release.GuardError("rollback failed")]
    record = {}
    with pytest.raises(release.GuardError):
        release.apply(runner, prepared, record)
    assert not record.get("passed")
    assert runner.install.call_args.args == (runner.m["rollback"],)
    if failure == "rollback":
        assert "rollback failed" in record["rollback_error"]
    else:
        assert record["rollback_preserved"]


def test_stable_health_ignores_uptime_but_rejects_failed_service(manifest):
    runner = release.LocalRelease(manifest)
    runner.get = MagicMock(return_value=b'{"status":"ok","uptime":123}')
    assert runner.checks() == [{"name": "TLS", "fields": {"status": "ok"}}]
    runner.get.return_value = b'{"status":"ok","uptime":9876}'
    assert runner.checks() == [{"name": "TLS", "fields": {"status": "ok"}}]
    runner.get.assert_called_with("https://host.test:9501/health")
    runner.get.return_value = b'{"status":"failed"}'
    with pytest.raises(release.GuardError, match="Independent JSON"):
        runner.checks()
    manifest["checks"] = [
        {"name": "preview", "url": "https://host.test:5300/", "sha256": release.digest(b"static")}
    ]
    runner.get.return_value = b"static"
    assert runner.checks()[0]["name"] == "preview"
    runner.get.return_value = b"changed"
    with pytest.raises(release.GuardError, match="Static content"):
        runner.checks()


def test_correct_url_and_auth_scope_without_proxy_or_tls_bypass(manifest, monkeypatch):
    monkeypatch.setenv("FORGE_TOKEN", "test-token")
    runner = release.LocalRelease(manifest)
    opener = MagicMock()
    response = opener.open.return_value.__enter__.return_value
    response.status = 200
    response.read.return_value = b'{"status":"ok"}'
    runner.opener = opener
    assert runner.api("/health") == {"status": "ok"}
    assert opener.open.call_args.args[0].get_header("Authorization") == "Bearer test-token"
    runner.get("https://host.test:9501/health")
    assert opener.open.call_args.args[0].get_header("Authorization") is None
    with pytest.raises(release.GuardError):
        runner.get("file:///etc/passwd")
    response.status = 503
    with pytest.raises(release.GuardError):
        runner.get("https://host.test/health")


def test_readiness_waits_for_both_candidate_and_rollback(manifest, monkeypatch):
    runner = release.LocalRelease(manifest)
    runner.api = MagicMock(
        side_effect=[URLError("not up"), {"revision": "old"}, manifest["candidate"]["health"]]
    )
    monkeypatch.setattr(release.time, "sleep", MagicMock())
    assert runner.ready(manifest["candidate"]) == manifest["candidate"]["health"]
    runner.api.side_effect = [URLError("rollback starting"), manifest["rollback"]["health"]]
    assert runner.ready(manifest["rollback"]) == manifest["rollback"]["health"]
    runner.api.side_effect = URLError("down")
    monkeypatch.setattr(release.time, "monotonic", MagicMock(side_effect=[0, 100]))
    with pytest.raises(release.GuardError, match="deadline"):
        runner.ready(manifest["rollback"])


@pytest.mark.parametrize("bad", ["absent", "expired", "naive", "too_short"])
def test_window_rejects_invalid_or_insufficient_time(manifest, bad):
    now = datetime.now(UTC)
    if bad == "absent":
        manifest["window"] = None
    elif bad == "expired":
        manifest["window"]["end"] = (now - timedelta(seconds=1)).isoformat()
    elif bad == "naive":
        manifest["window"]["start"] = now.replace(tzinfo=None).isoformat()
    else:
        manifest["window"]["end"] = (now + timedelta(seconds=1)).isoformat()
    with pytest.raises(release.GuardError):
        release.LocalRelease(manifest).window()


@pytest.mark.skipif(sys.platform != "linux", reason="systemd process identity requires Linux /proc")
def test_unit_identity_hashes_no_environment_or_argv_export(manifest, monkeypatch):
    runner = release.LocalRelease(manifest)
    file = manifest["stable_files"][0]
    runner.ctl = MagicMock(
        return_value=f"MainPID={os.getpid()}\nKillMode=process\n"
        f"ActiveState=active\nFragmentPath={file}\nDropInPaths=\n"
    )
    observed = runner.unit()
    assert observed["files"][file] == release.digest(b"original")
    assert "argv_sha256" in observed["process"]
    runner.ctl.return_value = runner.ctl.return_value.replace(
        "KillMode=process", "KillMode=control-group"
    )
    with pytest.raises(release.GuardError, match="children"):
        runner.unit()


def test_real_atomic_install_and_fail_closed_systemctl(manifest, monkeypatch):
    runner = release.LocalRelease(manifest)
    runner.ctl = MagicMock(return_value=manifest["candidate"]["root"] + "/.venv/bin/python")
    runner.install(manifest["candidate"])
    assert Path(manifest["override_path"]).read_text() == manifest["candidate"]["override_content"]
    assert Path(manifest["override_path"]).stat().st_mode & 0o777 == 0o600
    runner.restart()
    assert runner.ctl.call_args.args == ("restart", "forge.service")
    monkeypatch.setattr(
        release.subprocess,
        "run",
        MagicMock(return_value=SimpleNamespace(returncode=0, stdout="ok")),
    )
    assert runner.run(["test-only"]) == "ok"
    release.subprocess.run.return_value.returncode = 1
    with pytest.raises(release.GuardError, match="Command failed"):
        runner.run(["test-only"])


def test_snapshot_includes_terminal_live_rows_and_archived_replay(manifest, monkeypatch):
    runner = release.LocalRelease(manifest)
    runner.unit = MagicMock(return_value={})
    runner.checks = MagicMock(return_value=[])
    monkeypatch.setattr(release, "protected_processes", lambda p: ({"22": {"pid": 22}}, {22}))
    state = {"stopped-but-live": {"pid": 22, "port": 1111, "state": "running"}}
    Path(manifest["state_file"]).write_text(json.dumps(state))
    runner.get = MagicMock(return_value=b'{"session_id":"stopped-but-live"}')
    rows = [
        {"id": "stopped-but-live", "status": "stopped"},
        {"id": "archived", "status": "archived"},
    ]
    runner.api = MagicMock(
        side_effect=[rows, {"turns": [{"content": "private-transcript-marker"}]}]
    )
    snapshot = runner.snapshot()
    assert snapshot["live_gateway_state"] == state
    assert snapshot["replay"]["archived"]["turns"] == 1
    assert "private-transcript-marker" not in json.dumps(snapshot)
    assert runner.api.call_args_list[0].args[0].endswith("?include_archived=true")
    Path(manifest["state_file"]).write_text("{}")
    with pytest.raises(release.GuardError, match="Uninventoried"):
        runner.snapshot()


def test_preflight_audits_candidate_and_safe_rollback_before_snapshot(manifest):
    runner = release.LocalRelease(manifest)
    runner.health = MagicMock()
    runner.release = MagicMock()
    runner.snapshot = MagicMock(return_value={"checked": True})
    assert runner.preflight() == {"checked": True}
    assert [c.args[0] for c in runner.release.call_args_list] == [
        manifest["candidate"],
        manifest["rollback"],
    ]
    runner.release.side_effect = release.GuardError("old backend unsafe")
    runner.snapshot.reset_mock()
    with pytest.raises(release.GuardError):
        runner.preflight()
    runner.snapshot.assert_not_called()


def test_release_audit_rejects_wrong_source_and_backend(manifest, monkeypatch):
    runner = release.LocalRelease(manifest)
    runner.run = MagicMock(side_effect=["new", ""])
    root = manifest["candidate"]["root"]
    audit = {
        "backend": "process",
        "modules": [root + "/src/volundr/main.py"],
        "health": manifest["candidate"]["health"],
    }
    proc = SimpleNamespace(returncode=0, stdout=json.dumps(audit))
    monkeypatch.setattr(release.subprocess, "run", MagicMock(return_value=proc))
    assert runner.release(manifest["candidate"]) == audit
    assert release.subprocess.run.call_args.kwargs["env"]["HOME"] != str(Path.home())
    for backend, modules in [("kubernetes", audit["modules"]), ("process", ["/other/main.py"])]:
        runner.run.side_effect = ["new", ""]
        proc.stdout = json.dumps({**audit, "backend": backend, "modules": modules})
        with pytest.raises(release.GuardError):
            runner.release(manifest["candidate"])


@pytest.mark.skipif(
    sys.platform != "linux", reason="systemd process inventory requires Linux /proc"
)
def test_process_inventory_includes_owned_anchor_and_exact_gateway_detection():
    processes, _ = release.protected_processes([os.getpid()])
    assert processes[str(os.getpid())] == release.identity(os.getpid())
    assert release.gateway_command(["python", "-m", "skuld"])
    assert release.gateway_command(["niuu", "platform", "skuld"])
    assert not release.gateway_command(["python", "-c", "text mentioning skuld"])


def test_cli_saves_failure_evidence_and_never_overwrites(manifest, tmp_path, monkeypatch):
    config = tmp_path / "manifest.json"
    config.write_text(json.dumps(manifest))
    evidence = tmp_path / "evidence.json"
    monkeypatch.setattr(sys, "argv", ["release", "prepare", str(config), str(evidence)])
    monkeypatch.setattr(release.LocalRelease, "preflight", lambda self: {"read_only": True})
    release.main()
    assert json.loads(evidence.read_text())["passed"] is True
    assert evidence.stat().st_mode & 0o777 == 0o600
    with pytest.raises(release.GuardError, match="overwrite"):
        release.main()
    failed = tmp_path / "failed.json"
    monkeypatch.setattr(sys, "argv", ["release", "apply", str(config), str(failed)])
    with pytest.raises(release.GuardError):
        release.main()
    assert json.loads(failed.read_text())["passed"] is False
