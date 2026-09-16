#!/usr/bin/env python3
"""Fail-closed, API-only local Forge release preparation and cutover.

``prepare`` is read-only except for its evidence file. ``apply`` requires that
exact evidence, a fresh maintenance window, and a preservation-safe rollback
release. It never signals session processes, starts sessions, sends prompts,
changes databases, or changes any systemd unit except its one owned drop-in.
See docs/forge/local-api-release.md for the manifest contract and limitations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener


class GuardError(RuntimeError):
    """A failed guard is never permission to continue to mutation."""


def require(condition, message):
    if not condition:
        raise GuardError(message)


def digest(value):
    if not isinstance(value, bytes):
        value = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(value).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as out:
        try:
            out.write(json.dumps(value, indent=2).encode() + b"\n")
            out.flush()
            os.fsync(out.fileno())
            os.replace(out.name, path)
        finally:
            Path(out.name).unlink(missing_ok=True)


def identity(pid):
    root = Path(f"/proc/{int(pid)}")
    fields = (root / "stat").read_text().rsplit(")", 1)[1].split()
    require(fields[0] != "Z", f"Protected process {pid} is a zombie")
    return {
        "pid": int(pid),
        "start_ticks": fields[19],
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "argv_sha256": digest((root / "cmdline").read_bytes()),
    }


def gateway_command(argv):
    return any(argv[i : i + 2] == ["-m", "skuld"] for i in range(len(argv))) or (
        len(argv) > 2 and argv[-2:] == ["platform", "skuld"]
    )


def protected_processes(extra_pids):
    """Inventory current-user gateways/native owners, plus explicit postmasters.

    Transient tools/PG connection children are not ownership anchors. Operators
    add unknown harness owners to extra_pids; argv is hashed, never exported.
    """
    protected = {str(pid): identity(pid) for pid in extra_pids}
    gateways = set()
    for root in Path("/proc").iterdir():
        if not root.name.isdigit():
            continue
        try:
            if root.stat().st_uid != os.getuid():
                continue
            argv = (root / "cmdline").read_bytes().decode(errors="replace").split("\0")
            argv = [arg for arg in argv if arg]
            comm = (root / "comm").read_text().strip()
            is_gateway = gateway_command(argv)
            native = (
                comm.startswith("tmux")
                or comm == "claude"
                or ("app-server" in argv and any("codex" in Path(arg).name for arg in argv[:2]))
            )
            if not is_gateway and not native:
                continue
            protected[root.name] = identity(int(root.name))
            if is_gateway:
                gateways.add(int(root.name))
        except (FileNotFoundError, ProcessLookupError):
            continue
    return protected, gateways


class LocalRelease:
    def __init__(self, manifest):
        self.m = manifest
        self.opener = build_opener(ProxyHandler({}))  # System CA trust; no TLS bypass.

    def run(self, argv):
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=self.m["command_timeout_seconds"]
        )
        require(result.returncode == 0, f"Command failed ({result.returncode}): {argv[0]}")
        return result.stdout

    def ctl(self, *args):
        return self.run(["systemctl", "--user", *args])

    def get(self, url):
        require(urlsplit(url).scheme in {"http", "https"}, "Expected HTTP or trusted HTTPS URL")
        headers = {}
        if url.startswith(self.m["api_url"].rstrip("/") + "/") and os.environ.get("FORGE_TOKEN"):
            headers["Authorization"] = "Bearer " + os.environ["FORGE_TOKEN"]
        with self.opener.open(
            Request(url, headers=headers), timeout=self.m["http_timeout_seconds"]
        ) as response:
            require(response.status == 200, f"HTTP health failed: {urlsplit(url).path}")
            return response.read()

    def api(self, path):
        return json.loads(self.get(self.m["api_url"].rstrip("/") + path))

    def health(self, expected):
        actual = self.api("/health")
        require(all(actual.get(k) == v for k, v in expected.items()), "API build/health mismatch")
        require(
            actual.get("dirty") is False and actual.get("failed_plugins") == [],
            "API dirty or has failed plugins",
        )
        return actual

    def checks(self):
        result = []
        require(bool(self.m["checks"]), "Independent service checks must be explicit")
        for check in self.m["checks"]:
            body = self.get(check["url"])
            if "json_fields" in check:
                fields = check["json_fields"]
                require(bool(fields), "Stable JSON health fields must not be empty")
                observed = json.loads(body)
                require(
                    all(observed.get(k) == v for k, v in fields.items()),
                    f"Independent JSON health failed: {check['name']}",
                )
                result.append({"name": check["name"], "fields": fields})
                continue
            require("sha256" in check, "Expected stable JSON fields or explicit static body hash")
            require(digest(body) == check["sha256"], f"Static content changed: {check['name']}")
            result.append({"name": check["name"], "sha256": check["sha256"]})
        return result

    def unit(self):
        output = self.ctl(
            "show",
            self.m["unit"],
            "-p",
            "MainPID",
            "-p",
            "KillMode",
            "-p",
            "ActiveState",
            "-p",
            "FragmentPath",
            "-p",
            "DropInPaths",
        )
        props = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
        require(props["KillMode"] == "process", "Unit would signal session children")
        require(props["ActiveState"] == "active", "API unit is not active")
        paths = [props["FragmentPath"], *props["DropInPaths"].split()]
        return {
            "process": identity(int(props["MainPID"])),
            "files": {p: digest(Path(p).read_bytes()) for p in paths},
        }

    def release(self, release):
        root = Path(release["root"]).resolve()
        require(
            self.run(["git", "-C", str(root), "rev-parse", "HEAD"]).strip()
            == release["health"]["revision"],
            "Release revision changed",
        )
        require(
            not self.run(
                ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"]
            ).strip(),
            "Release has tracked edits",
        )
        # Audit imports and actual adapter/composition semantics using the
        # release's own environment, never the live host's settings/state file.
        code = """
import json,sys,tempfile
from pathlib import Path
from niuu.build_identity import build_identity
from volundr.config import Settings
from volundr.composition_builders import _create_pod_manager, _runtime_backend
import volundr.main, skuld.transports.codex_ws
with tempfile.TemporaryDirectory() as d:
 s=Settings(pod_manager={"adapter":"volundr.adapters.outbound.local_process.LocalProcessPodManager",
                        "kwargs":{"state_file":d+"/never-live.json"}})
 p=_create_pod_manager(s)
 print(json.dumps({"health":build_identity(),"backend":_runtime_backend(s,p),
  "modules":[str(Path(m.__file__).resolve()) for m in
             (volundr.main,skuld.transports.codex_ws)],"python":sys.executable}))
"""
        with tempfile.TemporaryDirectory() as home:
            env = {
                "HOME": home,
                "PATH": "/usr/bin:/bin",
                "LANG": "C.UTF-8",
                "PYTHONPATH": f"{root / 'src'}:{root}",
                "NIUU_CONFIG": home + "/absent",
            }
            result = subprocess.run(
                [str(root / ".venv/bin/python"), "-c", code],
                cwd=home,
                env=env,
                capture_output=True,
                text=True,
                timeout=self.m["command_timeout_seconds"],
            )
        require(result.returncode == 0, "Release composition/import audit failed")
        audit = json.loads(result.stdout)
        require(audit["backend"] == "process", "Release has unsafe local startup semantics")
        require(
            all(Path(p).is_relative_to(root / "src") for p in audit["modules"]),
            "Release imports another checkout",
        )
        require(
            all(
                audit["health"].get(k) == v
                for k, v in release["health"].items()
                if k != "failed_plugins"
            ),
            "Release source digest mismatch",
        )
        require(audit["health"]["dirty"] is False, "Release source is dirty")
        return audit

    def snapshot(self):
        unit = self.unit()
        processes, gateways = protected_processes(self.m["protected_pids"])
        state = json.loads(Path(self.m["state_file"]).read_text())
        live = {sid: row for sid, row in state.items() if row.get("pid") in gateways}
        require(
            {row["pid"] for row in live.values()} == gateways,
            "Uninventoried live gateway: cannot guarantee preservation",
        )
        for sid, row in live.items():
            health = json.loads(self.get(f"http://127.0.0.1:{row['port']}/health"))
            require(health.get("session_id") == sid, f"Gateway route identity mismatch: {sid}")
        rows = self.api("/api/v1/forge/sessions?include_archived=true")
        require(isinstance(rows, list), "Incomplete archived session inventory")
        fields = (
            "id",
            "status",
            "source",
            "parent_session_id",
            "parent_instance_id",
            "cli_session_id",
            "archived_at",
            "project_id",
            "definition",
            "coordination",
        )
        sessions = {r["id"]: {k: r.get(k) for k in fields} for r in rows}
        require(len(sessions) == len(rows), "Duplicate session identity in inventory")
        require(set(live) <= set(sessions), "Live gateway lacks a stored session")
        replay = {}
        for sid in self.m["replay_ids"]:
            # Select only immutable archived/stopped samples. Live transcript
            # growth is not corruption and must not be full-body-hash guarded.
            require(
                sessions[sid]["status"] in {"archived", "stopped"} and sid not in live,
                "Exact replay samples must be inactive",
            )
            body = self.api(f"/api/v1/forge/sessions/{sid}/conversation")
            turns = body["turns"]
            replay[sid] = {"turns": len(turns), "sha256": digest(turns)}
        return {
            "unit": unit,
            "processes": processes,
            "live_gateway_state": live,
            "sessions": sessions,
            "replay": replay,
            "checks": self.checks(),
            "stable_files": {p: digest(Path(p).read_bytes()) for p in self.m["stable_files"]},
        }

    def compare(self, before, after, *, restarted=False):
        for pid, original in before["processes"].items():
            require(after["processes"].get(pid) == original, f"Protected process changed: {pid}")
        for key in ("live_gateway_state", "sessions", "replay", "checks", "stable_files"):
            require(before[key] == after[key], f"Preservation mismatch: {key}")
        for path, sha in before["unit"]["files"].items():
            require(after["unit"]["files"].get(path) == sha, "Existing unit file changed")
        if restarted:
            require(before["unit"]["process"] != after["unit"]["process"], "API did not restart")
            require(before["processes"] == after["processes"], "Runtime owner inventory changed")
            require(
                set(after["unit"]["files"])
                == set(before["unit"]["files"]) | {self.m["override_path"]},
                "Unexpected systemd drop-in change",
            )
            return
        require(before["unit"] == after["unit"], "API unit/process changed before cutover")
        require(before["processes"] == after["processes"], "New runtime appeared before cutover")

    def window(self):
        window = self.m.get("window")
        require(bool(window), "No newly coordinated maintenance window")
        start, end = (datetime.fromisoformat(window[k]) for k in ("start", "end"))
        require(start.tzinfo is not None and end.tzinfo is not None, "Window must include timezone")
        now = datetime.now(UTC)
        require(start <= now < end, "Outside coordinated maintenance window")
        reserve = 2 * (self.m["command_timeout_seconds"] + self.m["ready_timeout_seconds"])
        require(
            (end - now).total_seconds() >= reserve, "Insufficient window for cutover and recovery"
        )

    def preflight(self):
        for key in (
            "command_timeout_seconds",
            "http_timeout_seconds",
            "ready_timeout_seconds",
            "poll_interval_seconds",
        ):
            require(self.m[key] > 0, "Release timing must be positive")
        require(
            Path(self.m["override_path"]).parent
            == Path.home() / ".config/systemd/user" / f"{self.m['unit']}.d",
            "Override must belong to selected user service",
        )
        require(not Path(self.m["override_path"]).exists(), "Owned override already exists")
        self.health(self.m["expected_health"])
        for name in ("candidate", "rollback"):
            release = self.m[name]
            root = release["root"]
            lines = release["override_content"].splitlines()
            require(
                len(lines) == 4
                and lines[0] == "[Service]"
                and lines[2] == "ExecStart="
                and lines[3].startswith("ExecStart="),
                "Only a source-only override is allowed",
            )
            require(
                f"WorkingDirectory={root}\n" in release["override_content"],
                "Override working directory mismatch",
            )
            require(
                root + "/.venv/bin/" in release["override_content"],
                "Override must select isolated environment",
            )
            self.release(release)
        return self.snapshot()

    def install(self, release):
        path = Path(self.m["override_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as out:
            try:
                out.write(release["override_content"].encode())
                out.flush()
                os.fsync(out.fileno())
                os.replace(out.name, path)
            finally:
                Path(out.name).unlink(missing_ok=True)
        self.ctl("daemon-reload")
        selected = self.ctl("show", self.m["unit"], "-p", "ExecStart", "--value")
        require(
            release["root"] + "/.venv/bin/" in selected,
            "Systemd did not select isolated release executable",
        )

    def restart(self):
        self.ctl("restart", self.m["unit"])

    def ready(self, release):
        deadline = time.monotonic() + self.m["ready_timeout_seconds"]
        while True:
            try:
                return self.health(release["health"])
            except (GuardError, URLError, TimeoutError, json.JSONDecodeError):
                require(time.monotonic() < deadline, "API readiness deadline exceeded")
                time.sleep(self.m["poll_interval_seconds"])


def prepare(runner):
    return {
        "manifest_sha256": digest(runner.m),
        "prepared_at": datetime.now(UTC).isoformat(),
        "before": runner.preflight(),
    }


def apply(runner, prepared, record):
    require(prepared["manifest_sha256"] == digest(runner.m), "Manifest changed after preparation")
    runner.window()
    before = runner.preflight()
    runner.compare(prepared["before"], before)
    # Every check above is inside this flow. No shell sequencing can skip a
    # failure and continue to install/restart as happened in the first rollout.
    runner.window()
    attempted = False
    try:
        attempted = True
        runner.install(runner.m["candidate"])
        runner.restart()
        record["candidate_health"] = runner.ready(runner.m["candidate"])
        after = runner.snapshot()
        runner.compare(before, after, restarted=True)
        record.update(passed=True, after=after)
    except BaseException:
        if attempted:
            # Never return to the known-bad old classifier. The separately
            # audited safety-only rollback contains the preservation fix too.
            try:
                runner.install(runner.m["rollback"])
                runner.restart()
                record["rollback_health"] = runner.ready(runner.m["rollback"])
                after = runner.snapshot()
                runner.compare(before, after, restarted=True)
                record["rollback_preserved"] = True
            except BaseException as exc:
                record["rollback_error"] = f"{type(exc).__name__}: {exc}"
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "apply"))
    parser.add_argument("manifest", type=Path)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--prepared", type=Path)
    args = parser.parse_args()
    require(not args.evidence.exists(), "Evidence exists; never overwrite an earlier attempt")
    runner = LocalRelease(json.loads(args.manifest.read_text()))
    record = {"passed": False, "mode": args.mode, "started_at": datetime.now(UTC).isoformat()}
    try:
        if args.mode == "prepare":
            record.update(prepare(runner), passed=True)
            return
        require(args.prepared is not None, "Apply requires saved preparation evidence")
        prepared = json.loads(args.prepared.read_text())
        require(prepared.get("passed") is True, "Preparation did not pass")
        apply(runner, prepared, record)
    except BaseException as exc:
        record["failure"] = {"type": type(exc).__name__, "detail": str(exc)}
        raise
    finally:
        record["finished_at"] = datetime.now(UTC).isoformat()
        save(args.evidence, record)


if __name__ == "__main__":
    main()
