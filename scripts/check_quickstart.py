"""Exercise the quick-start commands against a real, isolated local platform.

No fake runtimes or providers. The default check needs the real Claude CLI but
does not send a model request. --live additionally verifies the tutorial file
using the current Claude authentication (and can incur provider usage).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs/site/get-started/first-local-stack.md"


def command(name: str) -> str:
    matches = re.findall(
        rf"<!-- quickstart-check: {re.escape(name)} -->\s*```bash\n(.*?)\n```",
        GUIDE.read_text(),
        re.DOTALL,
    )
    if len(matches) != 1:
        raise ValueError(f"Expected one executable quick-start block: {name}")
    return matches[0]


def check_service_guides(base: str, env: dict[str, str], run: Path) -> None:
    """Run the service tutorials against the isolated host, including real ingest."""
    for page in (
        "reference/api.md",
        "reference/sessions.md",
        "get-started/model-routing-step.md",
        "get-started/durable-memory.md",
        "get-started/shared-discovery-and-topology.md",
    ):
        source = (ROOT / "docs/site" / page).read_text()
        commands = [
            block
            for block in re.findall(r"```bash\n(.*?)\n```", source, re.DOTALL)
            if block.startswith("curl ")
        ]
        if not commands:
            raise ValueError(f"No HTTP examples found in {page}")
        for block in commands:
            subprocess.run(
                ["bash", "-euc", block.replace("http://127.0.0.1:8080", base)],
                cwd=run,
                env=env,
                check=True,
                timeout=30,
                stdout=subprocess.DEVNULL,
            )
    sources = request(base, "/api/v1/mimir/sources")
    if not any(source["title"] == "Niuu onboarding note" for source in sources):
        raise RuntimeError("The knowledge guide did not persist its source")
    print("PASS: service guide HTTP commands and Mímir source ingestion/readback")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def request(base: str, path: str, data: dict | None = None) -> dict | list:
    body = json.dumps(data).encode() if data is not None else None
    req = Request(base + path, data=body, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=20) as response:
        return json.load(response)


def wait_until(check, process: subprocess.Popen, timeout: float, description: str):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Platform exited while waiting for {description}")
        try:
            result = check()
            if result:
                return result
        except (URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(0.5)
    raise TimeoutError(f"Timed out waiting for {description}")


def stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)
        raise RuntimeError("Platform did not stop after Ctrl+C") from None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--niuu", required=True, type=Path, help="Source or release executable")
    parser.add_argument("--live", action="store_true", help="Require real model file creation")
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    binary = args.niuu.resolve(strict=True)
    if not shutil.which("claude"):
        parser.error("Install the real Claude CLI before running this check")

    run = Path(tempfile.mkdtemp(prefix="niuu-quickstart-"))
    print(f"Quick-start evidence directory: {run}", flush=True)
    (run / "bin").mkdir()
    (run / "bin/niuu").symlink_to(binary)
    env = os.environ.copy()
    # Avoid reading an existing platform's configuration/database. Preserve the
    # user's HOME and Claude login; the live check uses their normal auth path.
    for key in list(env):
        if key.startswith(("NIUU_", "DATABASE__", "VOLUNDR_", "SKULD__", "MIMIR__")):
            env.pop(key)
    port = free_port()
    env.update(
        PATH=f"{run / 'bin'}{os.pathsep}{env['PATH']}",
        NIUU_CONFIG=str(run / "config.yaml"),
        MIMIR__PATH=str(run / "mimir"),
        NIUU_PGDATA_DIR=str(run / "pgdata"),
        NIUU_FORGE_STATE_FILE=str(run / "forge-state.json"),
        NIUU_SERVER__HOST="127.0.0.1",
        NIUU_SERVER__PORT=str(port),
        NIUU_POD_MANAGER__WORKSPACES_DIR=str(run / "workspaces"),
        NIUU_POD_MANAGER__SDK_PORT_START=str(free_port()),
        NIUU_QUICKSTART_URL=f"http://127.0.0.1:{port}",
    )
    for name in ("version", "init"):
        subprocess.run(
            ["bash", "-euc", command(name)],
            cwd=run,
            env=env,
            input="1\n" if name == "init" else None,
            text=True,
            check=True,
            timeout=30,
        )
    if not (run / "config.yaml").exists():
        raise RuntimeError("platform init ignored NIUU_CONFIG")
    if args.live:
        subprocess.run(
            ["bash", "-euc", command("authenticate")],
            cwd=run,
            env=env,
            check=True,
            timeout=args.timeout,
        )

    base = env["NIUU_QUICKSTART_URL"]
    session_id = None
    with (run / "platform.log").open("w") as log:
        process = subprocess.Popen(
            ["bash", "-euc", "exec " + command("start")],
            cwd=run,
            env=env,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        try:
            wait_until(lambda: request(base, "/health"), process, args.timeout, "health")
            subprocess.run(
                ["bash", "-euc", command("health")],
                cwd=run,
                env=env,
                check=True,
                timeout=30,
            )
            with urlopen(base + "/", timeout=20) as response:
                if b"<html" not in response.read().lower():
                    raise RuntimeError("The release does not serve the web UI")
            check_service_guides(base, env, run)
            definitions = request(base, "/api/v1/volundr/session-definitions")
            if not any(item["key"] == "skuldClaude" for item in definitions):
                raise RuntimeError("The documented Claude Code runtime is missing")
            payload = {
                "name": "niuu-quickstart",
                "model": args.model,
                "definition": "skuldClaude",
                "source": {"type": "git", "repo": "", "branch": ""},
            }
            if args.live:
                prompts = re.findall(
                    r"<!-- quickstart-check: prompt -->\s*```text\n(.*?)\n```",
                    GUIDE.read_text(),
                    re.DOTALL,
                )
                if len(prompts) != 1:
                    raise ValueError("Expected one tutorial prompt")
                payload["initial_prompt"] = prompts[0]
            session = request(base, "/api/v1/forge/sessions", payload)
            session_id = session["id"]
            path = f"/api/v1/forge/sessions/{session_id}"
            wait_until(
                lambda: request(base, path)["status"] == "running",
                process,
                args.timeout,
                "running session",
            )
            result_file = run / "workspaces" / session_id / "hello.txt"
            if args.live:
                wait_until(result_file.exists, process, args.timeout, "hello.txt from the agent")
                if result_file.read_bytes() != b"Hello from Niuu!\n":
                    raise RuntimeError("The agent's hello.txt does not match the tutorial")
            stopped = request(base, path + "/stop", {})
            if stopped["status"] != "stopped":
                raise RuntimeError("Session did not stop")
            if args.live and result_file.read_bytes() != b"Hello from Niuu!\n":
                raise RuntimeError("Stopping the session lost its workspace")
            session_id = None
        finally:
            try:
                if session_id and process.poll() is None:
                    request(base, f"/api/v1/forge/sessions/{session_id}/stop", {})
            finally:
                stop(process)
    print("PASS: documented commands, real PostgreSQL, web UI, session launch and stop, Ctrl+C")
    print(
        "PASS: live agent file creation and persistence"
        if args.live
        else "NOT TESTED: provider authentication and inference (run with --live)"
    )


if __name__ == "__main__":
    main()
