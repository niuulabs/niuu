"""Exercise rendered startup scripts against an authenticated Git HTTP server."""

import base64
import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml

from volundr.adapters.outbound.direct_k8s_pod_manager import DirectK8sPodManager
from volundr.domain.models import GitSource, PodSpecAdditions, Session, SessionSpec

ROOT = Path(__file__).resolve().parents[2]


def run(*args, **kwargs):
    return subprocess.run(args, capture_output=True, text=True, check=True, **kwargs)


@pytest.fixture
def git_remote(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("first-test-token")
    run("git", "init", "--bare", "--initial-branch=main", str(tmp_path / "remote.git"))
    run("git", "--git-dir", str(tmp_path / "remote.git"), "config", "http.receivepack", "true")
    seed = tmp_path / "seed"
    run("git", "init", "--initial-branch=main", str(seed))
    run(
        "git",
        "-C",
        str(seed),
        "-c",
        "user.name=Seed",
        "-c",
        "user.email=seed@test.local",
        "commit",
        "--allow-empty",
        "-m",
        "Seed",
    )
    run("git", "-C", str(seed), "push", str(tmp_path / "remote.git"), "main")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            expected = (
                "Basic "
                + base64.b64encode(f"x-access-token:{token_file.read_text()}".encode()).decode()
            )
            if self.headers.get("Authorization") != expected:
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="git"')
                self.end_headers()
                return
            url = urlsplit(self.path)
            env = {
                **os.environ,
                "GIT_PROJECT_ROOT": str(tmp_path),
                "GIT_HTTP_EXPORT_ALL": "1",
                "PATH_INFO": url.path,
                "QUERY_STRING": url.query,
                "REQUEST_METHOD": self.command,
                "REMOTE_USER": "git",
                "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
            }
            payload = self.rfile.read(int(env["CONTENT_LENGTH"]))
            response = subprocess.run(
                ["git", "http-backend"], env=env, input=payload, capture_output=True, check=True
            ).stdout
            header, body = response.split(b"\r\n\r\n", 1)
            self.send_response(200)
            for line in header.decode().splitlines():
                name, value = line.split(":", 1)
                self.send_header(name, value.strip())
            self.end_headers()
            self.wfile.write(body)

        do_POST = do_GET  # noqa: N815 - HTTP handler API

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/remote.git", token_file
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.mark.parametrize("backend", ["skuld", "skuld-planner", "direct", "openshell"])
def test_integration_token_survives_clone_for_commit_and_push(
    tmp_path, git_remote, backend, monkeypatch
):
    repo_url, token_file = git_remote
    workspace = tmp_path / "workspace"
    git = {
        "repoUrl": repo_url,
        "cloneUrl": repo_url,
        "branch": "feature",
        "baseBranch": "main",
        "userName": "Session User's $(false)",
        "userEmail": "session@test.local",
        "credentials": {
            "secretName": "stale-cluster-token",
            "username": "x-access-token",
            "tokenFile": str(token_file),
        },
    }
    mount = {"name": "selected-git", "mountPath": str(token_file), "readOnly": True}
    if backend == "openshell":
        from tests.test_adapters.test_openshell_gateway import (
            _FakeOpenShellGatewayClient,
            _import_adapter,
        )

        adapter = _import_adapter(monkeypatch)
        manager = adapter.OpenShellGatewayPodManager(
            client=_FakeOpenShellGatewayClient(adapter),
            sandbox_workspace=str(workspace),
        )
        session = Session(
            name="test", model="test", source=GitSource(repo=repo_url, branch="feature")
        )
        script = manager._workspace_bootstrap_script(
            session, SessionSpec(values={"git": git}, pod_spec=None)
        )
        assert "GIT_AUTH_TOKEN" not in script
    elif backend == "direct":
        session = Session(
            name="test", model="test", source=GitSource(repo=repo_url, branch="feature")
        )
        spec = SessionSpec(
            values={"git": git},
            pod_spec=PodSpecAdditions(
                volume_mounts=(mount,),
                annotations={"vault.hashicorp.com/agent-inject-containers": "skuld"},
            ),
        )
        manager = DirectK8sPodManager()
        container = next(
            c for c in manager._build_init_containers(session, spec) if c["name"] == "git-clone"
        )
        pod = manager._build_deployment_manifest(session, spec)["spec"]["template"]
        assert set(
            pod["metadata"]["annotations"]["vault.hashicorp.com/agent-inject-containers"].split(",")
        ) == {"skuld", "devrunner", "git-clone", "nginx", "vscode-reh"}
        script = container["args"][0].replace(
            f"/volundr/sessions/{session.id}/workspace", str(workspace)
        )
        assert not container["env"]
        assert mount in container["volumeMounts"]
        assert not any(
            e.get("valueFrom", {}).get("secretKeyRef", {}).get("name") == "github-token"
            for e in manager._build_env(session, spec)
        )
    else:
        if not shutil.which("helm"):
            pytest.skip("helm is required")
        values = {
            "git": git,
            "session": {"id": "test"},
            "extraVolumeMounts": [mount],
            "podAnnotations": {
                "vault.hashicorp.com/agent-inject": "true",
                "vault.hashicorp.com/agent-inject-containers": "skuld",
            },
        }
        if backend == "skuld":
            values["extraContainers"] = [{"name": "ravn-coder", "image": "test"}]
        rendered = run(
            "helm",
            "template",
            "test",
            str(ROOT / "charts" / backend),
            "-f",
            "-",
            input=yaml.safe_dump(values),
        )
        deployment = next(
            d for d in yaml.safe_load_all(rendered.stdout) if d and d["kind"] == "Deployment"
        )
        pod = deployment["spec"]["template"]
        container = next(c for c in pod["spec"]["initContainers"] if c["name"] == "git-clone")
        script = container["args"][0].replace("/volundr/sessions/test/workspace", str(workspace))
        assert not container.get("env")
        assert mount in container["volumeMounts"]
        assert (
            "git-clone"
            in pod["metadata"]["annotations"]["vault.hashicorp.com/agent-inject-containers"]
        )
        if backend == "skuld":
            ravn = next(c for c in pod["spec"]["containers"] if c["name"] == "ravn-coder")
            assert mount in ravn["volumeMounts"]
            assert (
                "ravn-coder"
                in pod["metadata"]["annotations"]["vault.hashicorp.com/agent-inject-containers"]
            )
        assert "stale-cluster-token" not in json.dumps(pod)

    # A clean environment prevents the developer's real Git credentials from participating.
    home = tmp_path / "home"
    home.mkdir()
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": str(tmp_path / "system-gitconfig"),
        "GIT_TERMINAL_PROMPT": "0",
    }
    if backend == "openshell":
        # Emulate OpenShell's transport authentication; real provider attachment and
        # token exchange are covered by the gateway adapter tests.
        env.update(
            {
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": f"http.{repo_url}.extraHeader",
                "GIT_CONFIG_VALUE_0": "Authorization: Basic "
                + base64.b64encode(f"x-access-token:{token_file.read_text()}".encode()).decode(),
            }
        )
    run("sh", "-c", script, env=env)
    assert (
        run("git", "-C", str(workspace), "branch", "--show-current", env=env).stdout.strip()
        == "feature"
    )
    config = (workspace / ".git" / "config").read_text()
    assert "first-test-token" not in config
    assert (
        run("git", "-C", str(workspace), "remote", "get-url", "origin", env=env).stdout.strip()
        == repo_url
    )

    # Fresh processes can commit and push after startup, even after token replacement.
    token_file.write_text("replacement-test-token")
    if backend == "openshell":
        env["GIT_CONFIG_VALUE_0"] = (
            "Authorization: Basic "
            + base64.b64encode(f"x-access-token:{token_file.read_text()}".encode()).decode()
        )
    run("git", "-C", str(workspace), "commit", "--allow-empty", "-m", "Runtime commit", env=env)
    run("git", "-C", str(workspace), "push", "origin", "HEAD:feature", env=env)
    assert (
        run(
            "git",
            "--git-dir",
            str(tmp_path / "remote.git"),
            "log",
            "feature",
            "-1",
            "--format=%ae",
            env=env,
        ).stdout.strip()
        == "session@test.local"
    )
    assert "replacement-test-token" not in (workspace / ".git" / "config").read_text()

    # A restart refreshes the helper without discarding existing work or identity.
    run("git", "-C", str(workspace), "config", "user.name", "Custom Author", env=env)
    run("sh", "-c", script, env=env)
    assert (
        run("git", "-C", str(workspace), "config", "user.name", env=env).stdout.strip()
        == "Custom Author"
    )
    for other_url in (
        "http://other.invalid/unrelated.git",
        repo_url.replace("remote.git", "other.git"),
    ):
        unrelated = subprocess.run(
            ["git", "-C", str(workspace), "credential", "fill"],
            input=f"url={other_url}\n\n",
            text=True,
            capture_output=True,
            env=env,
        )
        assert unrelated.returncode != 0
        assert "replacement-test-token" not in unrelated.stdout
    if backend == "openshell":
        return  # OpenShell refreshes tokens at its proxy, not through a mounted file.
    token_file.write_text("")
    failed = subprocess.run(["sh", "-c", script], env=env, capture_output=True, text=True)
    assert failed.returncode != 0
    assert "Selected Git integration token was not injected" in failed.stderr
