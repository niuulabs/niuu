"""The join and leave scripts a host runs: rendered for one code, valid POSIX sh."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from volundr.adapters.inbound.host_join_script import (
    HOST_CONFIG_PATH,
    NIUU_HOST_KEY_PATH,
    render_darwin_join_script,
    render_darwin_leave_script,
    render_join_script,
    render_leave_script,
)
from volundr.adapters.outbound.host_checks import DARWIN_FACTS_SH, FACTS_SH, LEAVE_PATH

_needs_sh = pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX sh")


def test_join_script_carries_the_code_once_and_quoted():
    script = render_join_script(forge_url="https://forge.example", code="abc'def")
    assert "FORGE_URL=https://forge.example" in script
    assert "CODE='abc'\"'\"'def'" in script
    assert script.count("abc") == 1
    assert f"CONFIG={HOST_CONFIG_PATH}" in script
    assert f"LEAVE={LEAVE_PATH}" in script
    assert "__" not in script.replace("__name__", "")


def test_an_unpinned_join_script_has_an_empty_pin_and_keeps_wget():
    script = render_join_script(forge_url="https://f", code="c")
    assert "PIN=''" in script
    assert "wget -qO-" in script


def test_a_pinned_join_script_posts_only_through_curl_with_the_pin():
    script = render_join_script(forge_url="https://f:8443", code="c", pin="sha256//a+b/c=")
    assert "PIN=sha256//a+b/c=" in script
    pinned = script[script.index('if [ -n "$PIN" ]; then') : script.index("elif command -v curl")]
    assert "only curl checks a pin" in pinned
    assert '-k --pinnedpubkey "$PIN"' in pinned
    assert "wget -" not in pinned


@_needs_sh
def test_a_pinned_join_without_curl_says_so_and_changes_nothing(tmp_path):
    # Only the tool block, with a PATH that has no curl: it fails before anything is written.
    script = render_join_script(forge_url="https://f", code="c", pin="sha256//x=")
    block = script[script.index("say() {") : script.index('if [ "$(uname -s)" = Darwin ]')]
    pinned = script[script.index('if [ -n "$PIN" ]; then') : script.index("elif command -v curl")]
    probe = f"PIN=sha256//x=\n{block}\n{pinned}fi\necho reached\n"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    result = subprocess.run(
        [shutil.which("sh"), "-c", probe],
        capture_output=True,
        cwd=tmp_path,
        env={"PATH": str(bin_dir)},
    )
    assert result.returncode == 1
    assert b"only curl checks a pin" in result.stderr
    assert b"reached" not in result.stdout
    assert list(tmp_path.iterdir()) == [bin_dir]


def test_join_script_registers_sshds_own_key_and_never_reconfigures_sshd():
    script = render_join_script(forge_url="https://f", code="c")
    assert "SSHD_KEY=/etc/ssh/ssh_host_ed25519_key" in script
    assert f"NIUU_KEY={NIUU_HOST_KEY_PATH}" in script
    assert "sshd_config" not in script
    assert "systemctl restart" not in script


def test_join_script_embeds_the_leave_script():
    script = render_join_script(forge_url="https://f", code="c")
    assert render_leave_script() in script
    assert " niuu-forge-$HOST_ID" in render_leave_script()


@_needs_sh
def test_rendered_scripts_are_valid_posix_sh():
    for script in (
        render_join_script(forge_url="https://f", code="c"),
        render_join_script(forge_url="https://f", code="c", pin="sha256//x="),
        render_leave_script(),
        render_darwin_join_script(),
        render_darwin_leave_script(),
    ):
        subprocess.run(["sh", "-n"], input=script.encode(), check=True)


@_needs_sh
def test_join_refuses_to_run_as_a_normal_user_before_changing_anything(tmp_path):
    if subprocess.run(["id", "-u"], capture_output=True, text=True).stdout.strip() == "0":
        pytest.skip("runs as root here")
    script = render_join_script(forge_url="https://f", code="c")
    result = subprocess.run(["sh"], input=script.encode(), capture_output=True, cwd=tmp_path)
    assert result.returncode == 1
    assert b"run this as root" in result.stderr


def test_a_mac_takes_its_own_branch_with_its_own_facts_and_leave():
    script = render_join_script(forge_url="https://f", code="c")
    darwin = render_darwin_join_script()
    assert 'if [ "$(uname -s)" = Darwin ]; then' in script
    assert darwin in script
    assert script.index(darwin) < script.index('[ "$(uname -s)" = Linux ]')
    assert DARWIN_FACTS_SH.strip() in darwin and FACTS_SH.strip() not in darwin
    assert render_darwin_leave_script() in darwin
    assert "dscl . -read" in render_darwin_leave_script()
    assert "getent" not in darwin
    assert "systemctl" not in darwin and "cgroup" not in darwin.replace("cgroup_v2", "")


def _shims(directory, programs):
    directory.mkdir(exist_ok=True)
    for name, body in programs.items():
        path = directory / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)
    return directory


#: Every tool the macOS join looks for, standing in for a Mac whose preflight passes.
_MAC_TOOLS = {
    name: "true"
    for name in (
        "ssh-keygen",
        "python3",
        "sudo",
        "dscl",
        "dseditgroup",
        "route",
        "ipconfig",
        "sysctl",
        "sw_vers",
        "awk",
        "sed",
        "curl",
        "sshd",
    )
}


@_needs_sh
class TestMacPreflight:
    """Each run stops at a check before the join changes anything: nothing here is root."""

    def _run(self, tmp_path, **programs):
        tools = {"id": "echo 0", "uname": "echo Darwin", "launchctl": "exit 113", **_MAC_TOOLS}
        tools.update({name: body for name, body in programs.items() if body is not None})
        for name in [name for name, body in programs.items() if body is None]:
            tools.pop(name)
        shims = _shims(tmp_path / "bin", tools)
        (shims / "sh").symlink_to(shutil.which("sh"))
        script = render_join_script(forge_url="https://f", code="c")
        return subprocess.run(
            [shutil.which("sh")],
            input=script.encode(),
            capture_output=True,
            cwd=tmp_path,
            env={"PATH": str(shims)},
        )

    def test_remote_login_must_be_on(self, tmp_path):
        result = self._run(tmp_path)
        assert result.returncode == 1
        assert b"Remote Login is off" in result.stderr

    def test_missing_tools_are_named(self, tmp_path):
        result = self._run(tmp_path, dscl=None, curl=None)
        assert result.returncode == 1
        assert b"missing: dscl curl;" in result.stderr

    def test_the_python3_stub_without_command_line_tools(self, tmp_path):
        result = self._run(tmp_path, python3="exit 1")
        assert result.returncode == 1
        assert b"xcode-select --install" in result.stderr

    def test_root_first(self, tmp_path):
        result = self._run(tmp_path, id="echo 501")
        assert result.returncode == 1
        assert b"run this as root" in result.stderr


def test_a_mac_posts_with_the_listeners_pin():
    script = render_join_script(forge_url="https://f", code="c", pin="sha256//x=")
    darwin = render_darwin_join_script()
    assert 'PIN="$PIN"' in script
    assert '--pinnedpubkey "$PIN"' in darwin
