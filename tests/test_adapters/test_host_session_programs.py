"""The guest program parts both host runtimes share: the session's storage and its boot."""

import json
import os
import subprocess
import sys

from volundr.adapters.outbound.host_session_programs import (
    AGENT_COMMANDS,
    FALLBACK_TOOLS,
    SESSION_BOOT,
    SLOT_STORAGE,
)


def _python(tmp_path):
    """A stand-in for the session's Python: the real one, except ``-m skuld`` records."""
    wrapper = tmp_path / "python"
    wrapper.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = -m ] && [ "$2" = skuld ]; then env > "$HOME/env.txt"; exit 0; fi\n'
        f'exec {sys.executable} "$@"\n'
    )
    wrapper.chmod(0o755)
    return str(wrapper)


def _boot(tmp_path, **env):
    return subprocess.run(
        ["/bin/sh", "-c", SESSION_BOOT],
        env={"PATH": "/usr/bin:/bin", "NIUU_SKULD_PYTHON": _python(tmp_path), **env},
        capture_output=True,
        text=True,
    )


def test_boot_turns_secrets_into_environment_and_links_then_runs_skuld(tmp_path):
    secrets = tmp_path / "secrets"
    (secrets / "user").mkdir(parents=True)
    (secrets / "user" / "claude.json").write_text(json.dumps({"token": "sk-oauth"}))
    (secrets / "user" / "gh.yml").write_text("hosts")
    (secrets / "manifest.json").write_text(
        json.dumps(
            {
                "env": {"CLAUDE_CODE_OAUTH_TOKEN": {"file": "claude.json", "key": "token"}},
                "files": {"/home/skuld/.config/gh/hosts.yml": {"file": "gh.yml"}},
            }
        )
    )
    (secrets / "env.sh").write_text("export FROM_ENV_SH=yes\n")
    home, workspace = tmp_path / "home", tmp_path / "workspace"
    home.mkdir()
    source = tmp_path / "token"
    source.write_text("t")

    result = _boot(
        tmp_path,
        HOME=str(home),
        WORKSPACE_DIR=str(workspace),
        TMPDIR=str(tmp_path / "tmp"),
        NIUU_SECRETS_DIR=str(secrets),
        NIUU_SESSION_PLACES=json.dumps({"/home/skuld": str(home), "/workspace": str(workspace)}),
        NIUU_SESSION_LINKS=json.dumps(
            [{"target": str(home / ".kube" / "token"), "source": str(source)}]
        ),
    )

    assert result.returncode == 0, result.stderr
    seen = dict(
        line.split("=", 1) for line in (home / "env.txt").read_text().splitlines() if "=" in line
    )
    assert seen["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-oauth"
    assert seen["FROM_ENV_SH"] == "yes"
    assert seen["CLAUDE_CONFIG_DIR"] == f"{home}/.claude"
    assert not {"NIUU_SESSION_LINKS", "NIUU_SECRETS_DIR", "NIUU_SKULD_PYTHON"} & set(seen)
    assert os.readlink(home / ".config" / "gh" / "hosts.yml") == str(secrets / "user" / "gh.yml")
    assert os.readlink(home / ".kube" / "token") == str(source)
    assert workspace.is_dir() and (tmp_path / "tmp").is_dir()


def test_boot_stops_when_the_secrets_cannot_be_delivered(tmp_path):
    secrets = tmp_path / "secrets"
    (secrets / "user").mkdir(parents=True)
    (secrets / "user" / "x").write_text("x")
    (secrets / "manifest.json").write_text(json.dumps({"files": {"/etc/x": {"file": "x"}}}))

    result = _boot(
        tmp_path,
        HOME=str(tmp_path),
        WORKSPACE_DIR=str(tmp_path / "w"),
        NIUU_SECRETS_DIR=str(secrets),
        NIUU_SESSION_PLACES=json.dumps({"/home/skuld": str(tmp_path)}),
    )

    assert result.returncode != 0
    assert "lies outside the session home and workspace" in result.stderr
    assert not (tmp_path / "env.txt").exists()


def test_boot_defaults_to_the_container_secrets_directory(tmp_path):
    result = _boot(tmp_path, HOME=str(tmp_path), WORKSPACE_DIR=str(tmp_path / "w"))

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "env.txt").exists()


def _storage(tmp_path, *calls):
    """Run ``SLOT_STORAGE`` with subprocess and chown/fchown recorded, as a non-root user."""
    program = (
        "import json, os, subprocess, sys\n"
        "ran = []\n"
        "subprocess.run = lambda args, **kw: ran.append(args)\n"
        "os.fchown = lambda fd, uid, gid: None\n"
        + SLOT_STORAGE
        + "\n"
        + "\n".join(calls)
        + "\nprint(json.dumps(ran))\n"
    )
    return subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, cwd=tmp_path
    )


def test_storage_checks_the_marker_owns_the_storage_and_clones_as_the_slot(tmp_path):
    root = tmp_path / "session"
    root.mkdir()
    (root / ".allocation").write_text("alloc")

    result = _storage(
        tmp_path,
        f"print(prepare_storage({str(root)!r}, 'alloc', 61000, 'https://g/r', 'main', "
        "('home/.cache/containers',)))",
    )

    assert result.returncode == 0, result.stderr
    ran = json.loads(result.stdout.splitlines()[-1])
    assert ran[0][:4] == ["git", "clone", "--branch", "main"]
    assert ran[1][:2] == ["find", str(root)]
    assert ["-path", str(root / "home/.cache/containers"), "-prune", "-o"] == ran[1][2:6]
    assert (root / "workspace").is_dir() and (root / "home").is_dir()


def test_storage_refuses_an_unprepared_or_foreign_allocation(tmp_path):
    root = tmp_path / "session"
    root.mkdir()
    missing = _storage(tmp_path, f"prepare_storage({str(root)!r}, 'a', 1, '', '')")
    assert "Session storage was not prepared" in missing.stderr
    (root / ".allocation").write_text("other")
    foreign = _storage(tmp_path, f"prepare_storage({str(root)!r}, 'a', 1, '', '')")
    assert "Workspace allocation mismatch" in foreign.stderr


def test_storage_never_follows_a_link_the_slot_left(tmp_path):
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "trap").symlink_to(tmp_path / "elsewhere")
    result = _storage(tmp_path, f"owned({str(tmp_path / 'trap')!r}, 0o700, 1)")
    assert result.returncode != 0


def test_nix_is_required_by_name_of_the_machine(tmp_path):
    result = _storage(tmp_path, "require_nix('Mac')")
    if os.path.exists("/nix/var/nix/daemon-socket/socket"):
        assert result.returncode == 0
    else:
        assert "This Mac has no Nix daemon" in result.stderr


def test_one_list_of_commands_for_both_platforms():
    assert "claude" in AGENT_COMMANDS and "muse" in AGENT_COMMANDS
    assert not set(AGENT_COMMANDS) & set(FALLBACK_TOOLS)
