"""Focused checks for the SSH-hosted OpenShell runtime contract."""

import base64
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from volundr.adapters.outbound import ssh_openshell_vm_runtime as module
from volundr.domain.compute import BootstrapFile, MachineBootstrap
from volundr.domain.models import GitSource, PodSpecAdditions, Session, SessionSpec


def test_sandbox_name_is_deterministic_and_within_openshell_limit():
    lease = SimpleNamespace(id=UUID("03395171-ed7e-4728-963e-64c3f44c7865"))

    name = module.SshOpenShellVmRuntime._sandbox_name(lease)

    assert name == "niuu-03395171ed7e47"
    assert len(name) == 19


def test_session_bootstrap_adapts_paths_policy_and_platform_without_installing(tmp_path: Path):
    key = Ed25519PrivateKey.generate()
    private = tmp_path / "key"
    public = tmp_path / "key.pub"
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.OpenSSH,
            serialization.NoEncryption(),
        )
    )
    public.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.OpenSSH,
            serialization.PublicFormat.OpenSSH,
        )
    )
    runtime = module.SshOpenShellVmRuntime(
        ssh_private_key_file=str(private),
        ssh_public_key_file=str(public),
        data_dir=str(tmp_path / "runtime"),
        skuld_image="test/skuld@sha256:" + "a" * 64,
        sandbox_platform_url="https://platform.test/",
        policy="version: 1\nnetwork_policies: {}\n",
        host_prepared=True,
    )
    runtime.configure_credentials(AsyncMock())
    session = Session(name="test", owner_id="owner", tenant_id="tenant", source=GitSource())
    spec = SessionSpec(values={}, pod_spec=PodSpecAdditions())

    bootstrap = runtime.bootstrap(session, spec, MachineBootstrap())
    payload = json.loads(
        next(item.content for item in bootstrap.files if item.path == module._LAUNCH)
    )

    assert payload["environment"]["WORKSPACE_DIR"] == module._OPEN_SHELL_WORKSPACE
    assert payload["environment"]["HOME"] == module._OPEN_SHELL_HOME
    assert payload["environment"]["CODEX_HOME"] == module._OPEN_SHELL_CODEX_HOME
    assert payload["environment"]["CLAUDE_CONFIG_DIR"] == module._OPEN_SHELL_CLAUDE_HOME
    assert payload["environment"]["SKULD_BOOTSTRAP_FOREGROUND"] == "true"
    assert payload["environment"]["SKULD__VOLUNDR_API_URL"] == "https://platform.test"
    assert payload["policy_content"] == "version: 1\nnetwork_policies: {}\n"
    assert "policy_file" not in payload
    assert not any(item.path == "/etc/niuu/openshell-policy.yaml" for item in bootstrap.files)


def test_installed_skuld_wrapper_keeps_resumed_cli_state_in_persistent_home(tmp_path: Path):
    home = tmp_path / "home"
    codex_home = home / ".codex"
    claude_home = home / ".claude"
    codex_home.mkdir(parents=True)
    claude_home.mkdir()
    capture = tmp_path / "environment"
    python = tmp_path / "python"
    python.write_text(
        '#!/bin/sh\nprintf "%s|%s\\n" "$CODEX_HOME" "$CLAUDE_CONFIG_DIR" >> "$CAPTURE"\n'
    )
    python.chmod(0o755)
    wrapper = Path(__file__).parents[2] / "scripts" / "openshell-run-installed-skuld.sh"

    result = subprocess.run(
        ["sh", str(wrapper)],
        capture_output=True,
        check=False,
        env={
            **os.environ,
            "HOME": str(home),
            "CODEX_HOME": str(codex_home),
            "CLAUDE_CONFIG_DIR": str(claude_home),
            "SKULD_PYTHON_BIN": str(python),
            "SKULD_BOOTSTRAP_FOREGROUND": "true",
            "CAPTURE": str(capture),
        },
    )

    assert result.returncode == 0, result.stderr
    assert capture.read_text().splitlines() == [
        f"{codex_home}|{claude_home}",
        f"{codex_home}|{claude_home}",
    ]


def test_installer_validates_artifacts_after_default_gateway_start_failure():
    script = module._INSTALL_OPENSHELL

    assert "subprocess.run(['sh',str(installer)],check=False" in script
    assert "missing=[name for name in ('openshell','openshell-gateway')" in script
    assert "OpenShell installer did not install:" in script
    assert "GatewayPorts clientspecified" in script
    assert "run('sudo','-n','sshd','-t')" in script
    assert "run('sudo','-n','systemctl','restart','ssh')" in script
    # Gateway config schema 2, with the driver config the bind mounts need.
    assert "version = 2" in script and "version = 1" not in script
    assert 'compute_driver = "podman"' in script and "compute_drivers" not in script
    assert "allow_driver_config = true" in script
    assert "[openshell.drivers.podman.resource_admission]\nenabled = false" in script
    # A pinned, digest-checked installer instead of upstream main.
    assert "NVIDIA/OpenShell/main/install.sh" not in script
    assert "NVIDIA/OpenShell/{pinned_version}/install.sh" in script
    assert "hashlib.sha256(installer.read_bytes()).hexdigest()" in script
    assert "'OPENSHELL_VERSION':pinned_version" in script
    assert 'socket_path = "/run/user/{os.getuid()}/podman/podman.sock"' in script
    assert 'grpc_endpoint = "https://host.containers.internal:17670"' in script
    assert "server_san='host.containers.internal'" in script
    assert "OpenShell requires Podman 5 or newer" in script
    assert "user@{uid}.service.d/90-niuu-openshell.conf" in script
    assert "'restart',f'user@{uid}.service'" in script
    assert "OpenShell requires delegated CPU cgroup control" in script
    assert "stage='host-cgroup'" in script
    assert "stage='gateway-start'" in script
    assert "dockerd-rootless-setuptool.sh" not in script
    assert "run('systemctl','--user','restart','openshell-gateway')" in script
    assert "raise RuntimeError('OpenShell gateway did not become ready')" in script


def test_start_script_reports_only_allowlisted_stage_name():
    assert "NIIU_OPEN_SHELL_STAGE:'+stage" in module._START_OPENSHELL
    assert "stage='sandbox-create'" in module._START_OPENSHELL
    assert "f'{os.getuid()}:{os.getgid()}'" in module._START_OPENSHELL
    assert "'podman','unshare','chown','-R',sandbox_owner" in module._START_OPENSHELL
    assert module._START_OPENSHELL.index("stage='uid-map'") < module._START_OPENSHELL.index(
        "existing=subprocess.run"
    )
    assert "'/proc/{daemon_pid}/{kind}_map'" not in module._START_OPENSHELL
    assert "stage+='-'+category" in module._START_OPENSHELL
    assert "raise RuntimeError('OpenShell sandbox creation failed')" in module._START_OPENSHELL
    assert "diagnostic=diagnostic.replace(str(value),'<redacted>')" in module._START_OPENSHELL
    assert "NIIU_OPEN_SHELL_DETAIL:" in module._START_OPENSHELL
    assert "stage='secret-staging'" in module._START_OPENSHELL
    assert "'-m','0640','-o',str(os.getuid())" in module._START_OPENSHELL
    assert "'podman','unshare','chown',sandbox_owner,str(staged)" in module._START_OPENSHELL
    assert "json.dumps({'podman':{'mounts':mounts}})" in module._START_OPENSHELL
    assert "args.extend(['--provider',provider])" in module._START_OPENSHELL
    assert "p.get('policy_content')" in module._START_OPENSHELL
    assert "runtime_policy.unlink(missing_ok=True)" in module._START_OPENSHELL
    assert "'--from',p['image'],'--no-tty','--no-auto-providers'" in module._START_OPENSHELL
    assert "args.extend(['--','sleep','infinity'])" in module._START_OPENSHELL
    assert "'--detach'" not in module._START_OPENSHELL
    assert "'--no-credential-warnings'" not in module._START_OPENSHELL
    assert "stage='workload-start'" in module._START_OPENSHELL
    assert "'openshell','sandbox','exec','--name',name,'--no-tty','--'" in (module._START_OPENSHELL)
    assert "'Restart=on-failure'" in module._START_OPENSHELL
    assert "'target':p['workspace_target']" in module._START_OPENSHELL
    assert "stage='forward'" in module._START_OPENSHELL
    assert "['openshell','forward','start',str(p['broker_port']),name,'-d']" in (
        module._START_OPENSHELL
    )
    assert "check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL" in (
        module._START_OPENSHELL
    )


def test_start_script_remaps_mounts_before_restarting_existing_sandbox(tmp_path: Path):
    root = tmp_path / "session"
    (root / "workspace").mkdir(parents=True)
    (root / "home").mkdir()
    calls = tmp_path / "calls"
    script = module._START_OPENSHELL.replace(
        "pathlib.Path('/var/lib/niuu/session')", f"pathlib.Path({str(root)!r})"
    )
    binary = tmp_path / "bin"
    binary.mkdir()
    executable = (
        "#!/bin/sh\n"
        'printf "%s %s\\n" "$(basename "$0")" "$*" >> "$CALLS"\n'
        'if [ "$(basename "$0") $1 $2" = "openshell sandbox get" ]; then exit 0; fi\n'
        "exit 0\n"
    )
    for name in ("openshell", "podman", "sudo", "systemctl", "systemd-run"):
        path = binary / name
        path.write_text(executable)
        path.chmod(0o755)
    payload = {
        "sandbox_name": "sandbox",
        "sandbox_uid": 998,
        "sandbox_gid": 998,
        "secret_mounts": [],
        "sandbox_command": ["run-skuld"],
        "workload_unit": "skuld.service",
        "broker_port": 8081,
    }

    result = subprocess.run(
        [sys.executable, "-c", script],
        input=json.dumps(payload),
        capture_output=True,
        check=False,
        text=True,
        env={**os.environ, "CALLS": str(calls), "PATH": f"{binary}:{os.environ['PATH']}"},
    )

    assert result.returncode == 0, result.stderr
    recorded = calls.read_text().splitlines()
    normalize = next(
        index for index, call in enumerate(recorded) if call.startswith("sudo -n chown -R ")
    )
    remap = next(
        index
        for index, call in enumerate(recorded)
        if call.startswith("podman unshare chown -R 998:998 ")
    )
    lookup = recorded.index("openshell sandbox get sandbox")
    restart = recorded.index("openshell sandbox start sandbox")
    assert normalize < remap < lookup < restart
    assert "'--credential',provider['credential_env']" in module._CONFIGURE_PROVIDERS
    assert "provider['credential_value']=''" in module._CONFIGURE_PROVIDERS
    assert "'openshell','provider','delete',provider" in module._STOP_OPENSHELL


def test_stop_script_reports_safe_cleanup_phases():
    script = module._STOP_OPENSHELL

    for stage in (
        "allocation-marker",
        "sandbox-delete",
        "sandbox-delete-verify",
        "provider-delete",
        "provider-delete-verify",
        "provider-profile-delete",
    ):
        assert f"stage='{stage}'" in script
    assert "NIIU_OPEN_SHELL_STAGE:'+stage" in script


def _stop_detail(result):
    prefix = b"NIIU_OPEN_SHELL_DETAIL:"
    line = next(line for line in result.stderr.splitlines() if line.startswith(prefix))
    return base64.b64decode(line[len(prefix) :]).decode()


def test_stop_script_accepts_failed_delete_after_authoritative_absence(tmp_path: Path):
    marker = tmp_path / ".allocation"
    allocation = str(uuid4())
    marker.write_text(allocation)
    script = module._STOP_OPENSHELL.replace(
        "pathlib.Path('/var/lib/niuu/session/.allocation')",
        f"pathlib.Path({str(marker)!r})",
    )
    binary = tmp_path / "bin"
    binary.mkdir()
    (binary / "openshell").write_text(
        "#!/bin/sh\n"
        'if [ "$1 $2" = "sandbox delete" ]; then '
        'echo "deadline exceeded sensitive" >&2; exit 7; fi\n'
        'if [ "$1 $2" = "sandbox list" ]; then '
        'printf \'{"sandboxes":[],"next_page_token":""}\\n\'; exit 0; fi\n'
        "exit 0\n"
    )
    (binary / "systemctl").write_text("#!/bin/sh\nexit 0\n")
    for executable in binary.iterdir():
        executable.chmod(0o755)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            allocation,
            "sandbox",
            "8081",
            "unit",
            "[]",
            "0.3",
            "0.01",
            "0.1",
            "1000",
        ],
        capture_output=True,
        check=False,
        env={**os.environ, "PATH": f"{binary}:{os.environ['PATH']}"},
    )

    assert result.returncode == 0
    assert b"sensitive" not in result.stderr


def test_stop_script_reports_allowlisted_reason_when_sandbox_remains(tmp_path: Path):
    marker = tmp_path / ".allocation"
    allocation = str(uuid4())
    marker.write_text(allocation)
    script = module._STOP_OPENSHELL.replace(
        "pathlib.Path('/var/lib/niuu/session/.allocation')",
        f"pathlib.Path({str(marker)!r})",
    )
    binary = tmp_path / "bin"
    binary.mkdir()
    (binary / "openshell").write_text(
        "#!/bin/sh\n"
        'if [ "$1 $2" = "sandbox delete" ]; then exit 0; fi\n'
        'if [ "$1 $2" = "sandbox list" ]; then '
        'printf \'{"sandboxes":[{"name":"sandbox"}],"next_page_token":""}\\n\'; exit 0; fi\n'
        "exit 0\n"
    )
    (binary / "systemctl").write_text("#!/bin/sh\nexit 0\n")
    for executable in binary.iterdir():
        executable.chmod(0o755)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            allocation,
            "sandbox",
            "8081",
            "unit",
            "[]",
            "0.3",
            "0.01",
            "0.1",
            "1000",
        ],
        capture_output=True,
        check=False,
        env={**os.environ, "PATH": f"{binary}:{os.environ['PATH']}"},
    )

    assert result.returncode != 0
    assert b"NIIU_OPEN_SHELL_STAGE:sandbox-delete-verify" in result.stderr
    assert "delete=accepted" in _stop_detail(result)
    assert "last_presence=present" in _stop_detail(result)
    assert "deadline_exceeded=True" in _stop_detail(result)
    assert b"sensitive" not in result.stderr


def test_stop_script_rejects_malformed_inventory_as_absence(tmp_path: Path):
    marker = tmp_path / ".allocation"
    allocation = str(uuid4())
    marker.write_text(allocation)
    script = module._STOP_OPENSHELL.replace(
        "pathlib.Path('/var/lib/niuu/session/.allocation')",
        f"pathlib.Path({str(marker)!r})",
    )
    binary = tmp_path / "bin"
    binary.mkdir()
    (binary / "openshell").write_text(
        "#!/bin/sh\n"
        'if [ "$1 $2" = "sandbox delete" ]; then exit 1; fi\n'
        'if [ "$1 $2" = "sandbox list" ]; then '
        'printf \'{"sandboxes":[{}],"next_page_token":""}\\n\'; exit 0; fi\n'
        "exit 0\n"
    )
    (binary / "systemctl").write_text("#!/bin/sh\nexit 0\n")
    for executable in binary.iterdir():
        executable.chmod(0o755)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            allocation,
            "sandbox",
            "8081",
            "unit",
            "[]",
            "0.3",
            "0.01",
            "0.1",
            "1000",
        ],
        capture_output=True,
        check=False,
        env={**os.environ, "PATH": f"{binary}:{os.environ['PATH']}"},
    )

    assert result.returncode != 0
    assert "inventory=invalid-inventory" in _stop_detail(result)


def test_stop_script_rejects_inventory_transport_failure_before_provider_cleanup(
    tmp_path: Path,
):
    marker = tmp_path / ".allocation"
    calls = tmp_path / "calls"
    allocation = str(uuid4())
    marker.write_text(allocation)
    script = module._STOP_OPENSHELL.replace(
        "pathlib.Path('/var/lib/niuu/session/.allocation')",
        f"pathlib.Path({str(marker)!r})",
    )
    binary = tmp_path / "bin"
    binary.mkdir()
    (binary / "openshell").write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, sys\n"
        "args=sys.argv[1:]\n"
        "with pathlib.Path(os.environ['CALLS']).open('a') as stream: "
        "stream.write(' '.join(args)+'\\n')\n"
        "if args[:2] == ['sandbox','delete']: sys.exit(1)\n"
        "if args[:2] == ['sandbox','list']:\n"
        " print('connection refused sensitive',file=sys.stderr); sys.exit(1)\n"
        "sys.exit(0)\n"
    )
    (binary / "systemctl").write_text("#!/bin/sh\nexit 0\n")
    for executable in binary.iterdir():
        executable.chmod(0o755)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            allocation,
            "sandbox",
            "8081",
            "unit",
            '["provider"]',
            "0.5",
            "0.01",
            "0.2",
            "1000",
        ],
        capture_output=True,
        check=False,
        env={**os.environ, "CALLS": str(calls), "PATH": f"{binary}:{os.environ['PATH']}"},
    )

    assert result.returncode != 0
    assert "inventory=transport" in _stop_detail(result)
    assert b"sensitive" not in result.stderr
    assert "provider delete" not in calls.read_text()


def test_stop_script_checks_later_inventory_pages_before_provider_cleanup(tmp_path: Path):
    marker = tmp_path / ".allocation"
    calls = tmp_path / "calls"
    allocation = str(uuid4())
    marker.write_text(allocation)
    script = module._STOP_OPENSHELL.replace(
        "pathlib.Path('/var/lib/niuu/session/.allocation')",
        f"pathlib.Path({str(marker)!r})",
    )
    binary = tmp_path / "bin"
    binary.mkdir()
    (binary / "openshell").write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "args=sys.argv[1:]\n"
        "with pathlib.Path(os.environ['CALLS']).open('a') as stream: "
        "stream.write(' '.join(args)+'\\n')\n"
        "if args[:2] == ['sandbox','delete']: sys.exit(0)\n"
        "if args[:2] == ['sandbox','list']:\n"
        " first='--page-token' not in args\n"
        " page=({'sandboxes':[{'name':f'other-{i}'} for i in range(1000)],"
        "'next_page_token':'page-2'} if first else "
        "{'sandboxes':[{'name':'sandbox'}],'next_page_token':''})\n"
        " print(json.dumps(page)); sys.exit(0)\n"
        "sys.exit(0)\n"
    )
    (binary / "systemctl").write_text("#!/bin/sh\nexit 0\n")
    for executable in binary.iterdir():
        executable.chmod(0o755)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            allocation,
            "sandbox",
            "8081",
            "unit",
            '["provider"]',
            "0.5",
            "0.01",
            "0.2",
            "1000",
        ],
        capture_output=True,
        check=False,
        env={**os.environ, "CALLS": str(calls), "PATH": f"{binary}:{os.environ['PATH']}"},
    )

    recorded = calls.read_text()
    assert result.returncode != 0
    # The CLI pages by token now (--limit/--offset are gone).
    assert "sandbox list --page-size 1000 --output json" in recorded
    assert "sandbox list --page-size 1000 --page-token page-2 --output json" in recorded
    assert "provider delete" not in recorded


def _bare_runtime():
    """A runtime without SshContainerVmRuntime's setup, carrying its OpenShell pin."""
    runtime = object.__new__(module.SshOpenShellVmRuntime)
    runtime._openshell_version = module.DEFAULT_OPENSHELL_VERSION
    runtime._openshell_install_sha256 = module.DEFAULT_OPENSHELL_INSTALL_SHA256
    runtime._sandbox_inventory_page_size = module.DEFAULT_SANDBOX_INVENTORY_PAGE_SIZE
    return runtime


def test_native_claude_provider_uses_bearer_mediation_without_secret_value():
    specs = module._native_provider_specs(
        "session-1",
        {
            "openshell": {
                "credentialMappings": [
                    {
                        "credentialName": "claude-main",
                        "envMappings": {"CLAUDE_CODE_OAUTH_TOKEN": "token"},
                    }
                ]
            }
        },
    )

    assert len(specs) == 1
    spec = specs[0]
    assert len(spec["name"]) == 19
    assert spec["credential_name"] == "claude-main"
    assert spec["credential_field"] == "token"
    assert "credential_value" not in spec
    profile = module.yaml.safe_load(spec["profile"])
    assert profile["credentials"][0] == {
        "name": "CLAUDE_CODE_OAUTH_TOKEN",
        "description": "Niuu-managed CLAUDE_CODE_OAUTH_TOKEN",
        "env_vars": ["CLAUDE_CODE_OAUTH_TOKEN"],
        "required": True,
        "auth_style": "bearer",
        "header_name": "Authorization",
    }
    assert profile["endpoints"][0]["host"] == "api.anthropic.com"
    # Unspecified TLS: OpenShell v0.1.6 rejects "terminate" and terminates by itself.
    assert "tls" not in profile["endpoints"][0]
    assert "/opt/niuu/**" in profile["binaries"]


def test_native_gitlab_provider_uses_bearer_mediation_for_gitlab_binaries():
    specs = module._native_provider_specs(
        "session-1",
        {
            "openshell": {
                "credentialMappings": [
                    {
                        "credentialName": "gitlab-main",
                        "envMappings": {"GITLAB_TOKEN": "token"},
                    }
                ]
            }
        },
    )

    profile = module.yaml.safe_load(specs[0]["profile"])
    assert profile["credentials"][0]["auth_style"] == "bearer"
    assert profile["credentials"][0]["header_name"] == "Authorization"
    assert profile["endpoints"][0]["host"] == "gitlab.com"
    assert "/usr/bin/glab" in profile["binaries"]
    assert "/usr/bin/git" in profile["binaries"]


def test_explicit_policy_includes_native_provider_network_rules():
    specs = module._native_provider_specs(
        "session-1",
        {
            "openshell": {
                "credentialMappings": [
                    {
                        "credentialName": "claude-main",
                        "envMappings": {"CLAUDE_CODE_OAUTH_TOKEN": "token"},
                    }
                ]
            }
        },
    )

    merged = module.yaml.safe_load(
        module._policy_with_native_providers(
            "version: 1\nnetwork_policies:\n  platform:\n    name: platform\n",
            specs,
        )
    )

    assert merged["network_policies"]["platform"] == {"name": "platform"}
    provider = merged["network_policies"][specs[0]["name"]]
    assert provider["endpoints"][0]["host"] == "api.anthropic.com"
    assert {item["path"] for item in provider["binaries"]} >= {
        "/opt/niuu/**",
        "/usr/local/bin/claude",
    }
    assert "credential_value" not in provider


def test_native_provider_cleanup_does_not_need_a_provider_target():
    specs = module._native_provider_specs(
        "session-1",
        {
            "openshell": {
                "credentialMappings": [
                    {
                        "credentialName": "legacy",
                        "envMappings": {"UNKNOWN_TOKEN": "token"},
                    }
                ]
            }
        },
        resolve_targets=False,
    )

    assert len(specs) == 1
    assert list(specs[0]) == ["name"]


def test_native_provider_rejects_static_credential_files():
    with pytest.raises(ValueError, match="native providers"):
        module._native_provider_specs(
            "session-1",
            {
                "openshell": {
                    "credentialMappings": [
                        {
                            "credentialName": "claude-main",
                            "fileMappings": {"/sandbox/home/.claude/auth.json": "token"},
                        }
                    ]
                }
            },
        )


async def test_configure_native_provider_resolves_secret_only_at_start():
    runtime = _bare_runtime()
    runtime._credential_store = AsyncMock()
    runtime._policy = "version: 1\nnetwork_policies: {}\n"
    runtime._credential_store.get_value.return_value = {"token": "opaque-test-token"}
    runtime._ssh = lambda lease, bootstrap: ["ssh", "pinned-host"]
    runtime._run = AsyncMock()
    lease = SimpleNamespace(
        session_id=UUID("03395171-ed7e-4728-963e-64c3f44c7865"), owner_id="owner"
    )
    payload = {
        "openshell_credential_mappings": [
            {
                "credentialName": "claude-main",
                "envMappings": {"CLAUDE_CODE_OAUTH_TOKEN": "token"},
            }
        ]
    }

    providers = await runtime._configure_native_providers(lease, object(), payload)

    assert len(providers) == 1
    assert "openshell_credential_mappings" not in payload
    runtime._credential_store.get_value.assert_awaited_once_with("user", "owner", "claude-main")
    sent = json.loads(runtime._run.await_args.kwargs["data"])
    assert sent[0]["credential_value"] == "opaque-test-token"
    policy = module.yaml.safe_load(payload["policy_content"])
    assert policy["network_policies"][providers[0]]["endpoints"][0]["host"] == ("api.anthropic.com")
    assert "opaque-test-token" not in payload["policy_content"]
    assert runtime._run.await_args.args[0][:2] == ["ssh", "pinned-host"]


@pytest.mark.parametrize(
    ("credential_store", "value", "message"),
    [
        (None, None, "require a credential store"),
        (AsyncMock(), None, "is unavailable"),
        (AsyncMock(), {}, "lacks field"),
    ],
)
async def test_native_provider_fails_when_required_credential_is_unavailable(
    credential_store, value, message
):
    runtime = _bare_runtime()
    runtime._credential_store = credential_store
    runtime._policy = ""
    runtime._execute_remote = AsyncMock()
    if credential_store is not None:
        credential_store.get_value.return_value = value
    payload = {
        "openshell_credential_mappings": [
            {
                "credentialName": "claude-main",
                "envMappings": {"CLAUDE_CODE_OAUTH_TOKEN": "token"},
            }
        ]
    }
    lease = SimpleNamespace(session_id=uuid4(), owner_id="owner")

    with pytest.raises(RuntimeError, match=message):
        await runtime._configure_native_providers(lease, object(), payload)


async def test_ensure_openshell_uses_pinned_ssh_command():
    runtime = _bare_runtime()
    runtime._run = AsyncMock()
    runtime._ssh = lambda lease, bootstrap: ["ssh", "pinned-host"]

    await runtime._ensure_openshell(object(), object())

    argv = runtime._run.await_args.args[0]
    assert argv[:2] == ["ssh", "pinned-host"]
    assert argv[2].startswith("python3 -c ")
    assert argv[2].endswith(
        f" {module.DEFAULT_OPENSHELL_VERSION} {module.DEFAULT_OPENSHELL_INSTALL_SHA256}"
    )
    assert runtime._run.await_args.kwargs == {
        "safe_error_prefix": "NIIU_OPEN_SHELL_STAGE:",
        "safe_detail_prefix": "NIIU_OPEN_SHELL_DETAIL:",
    }


async def test_prepared_host_path_verifies_without_embedded_installation():
    runtime = _bare_runtime()
    runtime._host_prepared = True
    runtime._execute_remote = AsyncMock()

    await runtime._ensure_openshell(object(), object())

    command = runtime._execute_remote.await_args.args[2]
    pin = (module.DEFAULT_OPENSHELL_VERSION, module.DEFAULT_OPENSHELL_INSTALL_SHA256)
    assert command == runtime._user_python(module._VERIFY_OPENSHELL, *pin)
    assert command != runtime._user_python(module._INSTALL_OPENSHELL, *pin)


async def test_remote_execution_uses_injected_guest_access_port():
    runtime = _bare_runtime()
    runtime._guest_access = AsyncMock()
    runtime._guest_access.execute.return_value = SimpleNamespace(exit_code=7)
    lease, bootstrap = object(), object()

    result = await runtime._execute_remote(
        lease,
        bootstrap,
        "fixed-command",
        expected_exit_codes=(0, 7),
    )

    assert result == 7
    runtime._guest_access.execute.assert_awaited_once_with(
        lease,
        bootstrap,
        "fixed-command",
        expected_exit_codes=(0, 7),
    )


async def test_prepared_openshell_warm_verifies_then_pulls_runtime_image():
    runtime = _bare_runtime()
    runtime._host_prepared = True
    runtime._image = "test/skuld@sha256:" + "a" * 64
    runtime._guest_access = AsyncMock()
    runtime._guest_access.execute.return_value = SimpleNamespace(exit_code=0)
    runtime._execute_remote = AsyncMock(return_value=0)
    lease, bootstrap = object(), object()

    await runtime.warm(lease, bootstrap)

    assert runtime._guest_access.execute.await_args_list[0].args == (lease, bootstrap, "true")
    assert runtime._execute_remote.await_args.args[2] == runtime._user_python(
        module._VERIFY_OPENSHELL,
        module.DEFAULT_OPENSHELL_VERSION,
        module.DEFAULT_OPENSHELL_INSTALL_SHA256,
    )
    assert runtime._guest_access.execute.await_args_list[1].args[2] == (
        "podman pull " + runtime._image
    )


def _runtime_bootstrap() -> MachineBootstrap:
    return MachineBootstrap(
        files=(
            BootstrapFile(
                path=module._LAUNCH,
                content=json.dumps(
                    {
                        "image": "test/skuld@sha256:" + "a" * 64,
                        "environment": {},
                        "repo": "",
                        "branch": "",
                        "secret_mounts": [],
                        "openshell_credential_mappings": [],
                    }
                ),
            ),
        )
    )


async def test_start_stages_session_and_launches_without_host_install(tmp_path: Path):
    runtime = _bare_runtime()
    runtime._data = tmp_path
    runtime._sandbox_delete_timing = (120, 2, 30)
    runtime._broker_port = 8081
    runtime._sandbox_uid = 998
    runtime._sandbox_gid = 998
    runtime._sandbox_command = ("/usr/local/bin/openshell-run-installed-skuld",)
    runtime._policy = ""
    runtime._archive_excludes = ("home/.cache",)
    runtime._execute_remote = AsyncMock(return_value=0)
    runtime.target = AsyncMock()
    lease = SimpleNamespace(id=uuid4(), session_id=uuid4(), owner_id="owner")
    bootstrap = _runtime_bootstrap()

    await runtime.start(lease, bootstrap)

    commands = [call.args[2] for call in runtime._execute_remote.await_args_list]
    assert len(commands) == 3
    assert commands[0] == runtime._python(module._SESSION_FILES)
    assert commands[1] == runtime._python(
        module._PREPARE, str(lease.id), "empty", json.dumps(runtime._archive_excludes)
    )
    assert commands[2] == runtime._user_python(module._START_OPENSHELL)
    assert runtime._user_python(module._INSTALL_OPENSHELL) not in commands
    runtime.target.assert_awaited_once_with(lease, bootstrap)


async def test_start_passes_base_policy_through_user_owned_runtime_file_without_providers(
    tmp_path: Path,
):
    runtime = _bare_runtime()
    runtime._data = tmp_path
    runtime._sandbox_delete_timing = (120, 2, 30)
    runtime._broker_port = 8081
    runtime._sandbox_uid = 998
    runtime._sandbox_gid = 998
    runtime._sandbox_command = ("run-skuld",)
    runtime._policy = "version: 1\nnetwork_policies: {}\n"
    runtime._archive_excludes = ()
    runtime._execute_remote = AsyncMock(return_value=0)
    runtime.target = AsyncMock()
    lease = SimpleNamespace(id=uuid4(), session_id=uuid4(), owner_id="owner")
    bootstrap = _runtime_bootstrap()
    launch = json.loads(bootstrap.files[0].content)
    launch["policy_content"] = runtime._policy
    bootstrap = bootstrap.model_copy(
        update={"files": (bootstrap.files[0].model_copy(update={"content": json.dumps(launch)}),)}
    )

    await runtime.start(lease, bootstrap)

    start_call = runtime._execute_remote.await_args_list[-1]
    payload = json.loads(start_call.kwargs["data"])
    assert payload["providers"] == []
    assert payload["policy_content"] == runtime._policy
    assert "policy_file" not in payload


async def test_stop_archives_session_atomically_through_guest_access(tmp_path: Path):
    runtime = _bare_runtime()
    runtime._data = tmp_path
    runtime._sandbox_delete_timing = (120, 2, 30)
    runtime._broker_port = 8081
    runtime._archive_excludes = ("home/.cache", "home/.codex/tmp")
    runtime._close_tunnel = AsyncMock()
    lease = SimpleNamespace(id=uuid4(), session_id=uuid4())

    async def execute(lease, bootstrap, command, **kwargs):
        if destination := kwargs.get("stdout"):
            destination.write(b"preserved-session")
        return 0

    runtime._execute_remote = AsyncMock(side_effect=execute)
    await runtime.stop(lease, _runtime_bootstrap())

    archive = tmp_path / str(lease.session_id) / "session.tar"
    assert archive.read_bytes() == b"preserved-session"
    assert runtime._execute_remote.await_args_list[0].kwargs["safe_error_prefix"] == (
        module._OPEN_SHELL_ERROR_PREFIX
    )
    assert runtime._execute_remote.await_args_list[0].kwargs["safe_detail_prefix"] == (
        module._OPEN_SHELL_DETAIL_PREFIX
    )
    archive_command = runtime._execute_remote.await_args_list[1].args[2]
    assert "--exclude=./.allocation" in archive_command
    assert "--exclude=./home/.cache" in archive_command
    assert "--exclude=./home/.codex/tmp" in archive_command
    runtime._close_tunnel.assert_awaited_once_with(str(lease.id))


async def test_stop_before_restore_closes_tunnel_without_replacing_archive(tmp_path: Path):
    runtime = _bare_runtime()
    runtime._data = tmp_path
    runtime._sandbox_delete_timing = (120, 2, 30)
    runtime._broker_port = 8081
    runtime._execute_remote = AsyncMock(return_value=42)
    runtime._close_tunnel = AsyncMock()
    lease = SimpleNamespace(id=uuid4(), session_id=uuid4())
    archive = tmp_path / str(lease.session_id) / "session.tar"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"previous")

    await runtime.stop(lease, _runtime_bootstrap())

    assert archive.read_bytes() == b"previous"
    runtime._close_tunnel.assert_awaited_once_with(str(lease.id))


@pytest.mark.parametrize(
    "mode",
    ["delayed", "transport", "delete-timeout", "inventory-timeout", "stuck-inventory", "conflict"],
)
def test_stop_waits_for_authoritative_absence_before_provider_cleanup(tmp_path: Path, mode: str):
    marker = tmp_path / ".allocation"
    allocation = str(uuid4())
    marker.write_text(allocation)
    script = module._STOP_OPENSHELL.replace(
        "pathlib.Path('/var/lib/niuu/session/.allocation')", f"pathlib.Path({str(marker)!r})"
    )
    binary = tmp_path / "bin"
    binary.mkdir()
    calls = tmp_path / "calls"
    count = tmp_path / "count"
    (binary / "openshell").write_text(
        f"#!{sys.executable}\n"
        "import json, pathlib, sys, time\n"
        f"calls=pathlib.Path({str(calls)!r}); count=pathlib.Path({str(count)!r})\n"
        f"mode={mode!r}\n"
        "args=sys.argv[1:]\n"
        "with calls.open('a') as f: f.write(' '.join(args)+'\\n')\n"
        "if args[:2]==['sandbox','delete']:\n"
        " if mode=='delete-timeout': time.sleep(10)\n"
        " if mode=='conflict':\n"
        "  print('aborted sensitive',file=sys.stderr); sys.exit(7)\n"
        " sys.exit(0)\n"
        "if args[:2]==['sandbox','list']:\n"
        " n=int(count.read_text()) if count.exists() else 0\n"
        # Timeout may kill this fake CLI mid-write; preserve the previous count.
        " pending=count.with_suffix('.tmp')\n"
        " pending.write_text(str(n+1))\n"
        " pending.replace(count)\n"
        " if mode=='stuck-inventory' or (n==0 and mode=='inventory-timeout'): time.sleep(10)\n"
        " if n==0 and mode=='transport':\n"
        "  print('connection refused sensitive',file=sys.stderr); sys.exit(1)\n"
        " entries=[{'name':'sandbox','phase':'Deleting'}] if n<2 else []\n"
        " print(json.dumps({'sandboxes':entries,'next_page_token':''}))\n"
        " sys.exit(0)\n"
        "if args[:2]==['provider','get']: sys.exit(1)\n"
    )
    (binary / "systemctl").write_text("#!/bin/sh\nexit 0\n")
    for executable in binary.iterdir():
        executable.chmod(0o755)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            allocation,
            "sandbox",
            "8081",
            "unit",
            '["provider"]',
            "3",
            "0.01",
            # Per-command timeout: each fake CLI call is a fresh interpreter, and
            # a busy runner can take longer than a fraction of a second to start
            # one. A list call killed after it counted but before it printed is
            # read as a timeout and re-polled, so the count check would flake.
            "1.0",
            "1000",
        ],
        env={**os.environ, "PATH": f"{binary}:{os.environ['PATH']}"},
        capture_output=True,
        timeout=5,
    )
    if mode == "stuck-inventory":
        assert result.returncode != 0
        assert "inventory=deadline-exceeded" in _stop_detail(result)
        assert "deadline_exceeded=True" in _stop_detail(result)
        assert "provider delete" not in calls.read_text()
        return
    assert result.returncode == 0, result.stderr
    assert b"sensitive" not in result.stderr
    recorded = calls.read_text().splitlines()
    assert recorded.count("sandbox delete sandbox") == 1
    assert int(count.read_text()) == 3
    assert recorded.index("provider delete provider") > max(
        i for i, call in enumerate(recorded) if call.startswith("sandbox list")
    )


async def test_unconfirmed_delete_preserves_existing_archive(tmp_path: Path):
    from volundr.domain.vm_runtime import VmRuntimeStageError

    runtime = _bare_runtime()
    runtime._data = tmp_path
    runtime._sandbox_delete_timing = (120, 2, 30)
    runtime._broker_port = 8081
    runtime._close_tunnel = AsyncMock()
    runtime._execute_remote = AsyncMock(
        side_effect=VmRuntimeStageError("sandbox-delete-verify", "deletion-unconfirmed")
    )
    lease = SimpleNamespace(id=uuid4(), session_id=uuid4())
    archive = tmp_path / str(lease.session_id) / "session.tar"
    archive.parent.mkdir()
    archive.write_bytes(b"preserved")
    with pytest.raises(VmRuntimeStageError):
        await runtime.stop(lease, _runtime_bootstrap())
    assert archive.read_bytes() == b"preserved"
    runtime._execute_remote.assert_awaited_once()
    runtime._close_tunnel.assert_not_awaited()


@pytest.mark.parametrize("tls", ["terminate", "passthrough"])
def test_native_provider_rejects_tls_modes_openshell_removed(tls):
    with pytest.raises(ValueError, match="omit tls to keep automatic TLS termination"):
        module._native_provider_target(
            "RAVN_NATS_PASSWORD",
            {
                "endpoints": [{"host": "nats.example", "port": 4222, "tls": tls}],
                "binaries": ["/opt/niuu/bin/python"],
            },
        )


def test_installer_writes_a_schema_2_podman_gateway_config():
    script = module._INSTALL_OPENSHELL
    template = re.search(r"desired=f\'\'\'(.*?)\'\'\'", script, re.S).group(1)
    config = tomllib.loads(template.replace("{os.getuid()}", "1000"))

    assert config["openshell"]["version"] == 2
    assert config["openshell"]["gateway"]["compute_driver"] == "podman"
    podman = config["openshell"]["drivers"]["podman"]
    assert podman["enable_bind_mounts"] is True
    # The workspace/home bind mounts arrive through the sandbox driver config,
    # which OpenShell refuses unless allowed, and which admission cannot label.
    assert podman["allow_driver_config"] is True
    assert podman["resource_admission"] == {"enabled": False}


def _run_version_check(tmp_path: Path, banner: str) -> subprocess.CompletedProcess:
    binary = tmp_path / "bin"
    binary.mkdir()
    (binary / "openshell").write_text(f"#!/bin/sh\necho {banner!r}\nexit 0\n")
    for name in ("openshell-gateway", "podman"):
        (binary / name).write_text("#!/bin/sh\necho 5.0.0\nexit 0\n")
    for executable in binary.iterdir():
        executable.chmod(0o755)
    return subprocess.run(
        [sys.executable, "-c", module._VERIFY_OPENSHELL, "v0.1.6-pre.5", "unused"],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PATH": f"{binary}:/usr/bin:/bin"},
    )


def test_prepared_host_with_another_openshell_release_is_refused(tmp_path: Path):
    result = _run_version_check(tmp_path, "openshell 0.1.5")

    assert result.returncode != 0
    assert "runs 0.1.5; Niuu requires v0.1.6-pre.5" in result.stderr


@pytest.mark.parametrize("banner", ["openshell 0.1.6-pre.5", "openshell v0.1.6~pre.5"])
def test_prepared_host_on_the_pinned_release_passes_the_version_check(tmp_path: Path, banner: str):
    result = _run_version_check(tmp_path, banner)

    # The check after the version (the Podman socket) is what stops this fake host.
    assert "Niuu requires" not in result.stderr
    assert "Podman socket is unavailable" in result.stderr


def test_stop_script_rejects_an_inventory_that_repeats_its_page_token(tmp_path: Path):
    marker = tmp_path / ".allocation"
    allocation = str(uuid4())
    marker.write_text(allocation)
    script = module._STOP_OPENSHELL.replace(
        "pathlib.Path('/var/lib/niuu/session/.allocation')",
        f"pathlib.Path({str(marker)!r})",
    )
    binary = tmp_path / "bin"
    binary.mkdir()
    (binary / "openshell").write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "args=sys.argv[1:]\n"
        "if args[:2] == ['sandbox','list']:\n"
        " print(json.dumps({'sandboxes':[{'name':'other'}],'next_page_token':'again'}))\n"
        "sys.exit(0)\n"
    )
    (binary / "systemctl").write_text("#!/bin/sh\nexit 0\n")
    for executable in binary.iterdir():
        executable.chmod(0o755)

    result = subprocess.run(
        [sys.executable, "-c", script, allocation, "sandbox", "8081", "unit", "[]"]
        + ["0.5", "0.01", "0.2", "1000"],
        capture_output=True,
        check=False,
        env={**os.environ, "PATH": f"{binary}:{os.environ['PATH']}"},
    )

    assert result.returncode != 0
    assert "inventory=invalid-inventory" in _stop_detail(result)


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"openshell_version": ""}, "Pin the guest's OpenShell"),
        ({"openshell_install_sha256": ""}, "Pin the guest's OpenShell"),
        ({"sandbox_inventory_page_size": 0}, "sandbox_inventory_page_size must be positive"),
    ],
)
def test_runtime_requires_a_pinned_openshell_release(tmp_path: Path, override, message):
    key = Ed25519PrivateKey.generate()
    private = tmp_path / "key"
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.OpenSSH,
            serialization.NoEncryption(),
        )
    )
    public = tmp_path / "key.pub"
    public.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
        )
    )
    with pytest.raises(ValueError, match=message):
        module.SshOpenShellVmRuntime(
            ssh_private_key_file=str(private),
            ssh_public_key_file=str(public),
            data_dir=str(tmp_path / "runtime"),
            skuld_image="test/skuld@sha256:" + "a" * 64,
            **override,
        )
