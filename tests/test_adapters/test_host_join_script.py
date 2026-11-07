"""The join and leave scripts a host runs: rendered for one code, valid POSIX sh."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from volundr.adapters.inbound.host_join_script import (
    HOST_CONFIG_PATH,
    NIUU_HOST_KEY_PATH,
    render_join_script,
    render_leave_script,
)
from volundr.adapters.outbound.host_checks import LEAVE_PATH

_needs_sh = pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX sh")


def test_join_script_carries_the_code_once_and_quoted():
    script = render_join_script(forge_url="https://forge.example", code="abc'def")
    assert "FORGE_URL=https://forge.example" in script
    assert "CODE='abc'\"'\"'def'" in script
    assert script.count("abc") == 1
    assert f"CONFIG={HOST_CONFIG_PATH}" in script
    assert f"LEAVE={LEAVE_PATH}" in script
    assert "__" not in script.replace("__name__", "")


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
    for script in (render_join_script(forge_url="https://f", code="c"), render_leave_script()):
        subprocess.run(["sh", "-n"], input=script.encode(), check=True)


@_needs_sh
def test_join_refuses_to_run_as_a_normal_user_before_changing_anything(tmp_path):
    if subprocess.run(["id", "-u"], capture_output=True, text=True).stdout.strip() == "0":
        pytest.skip("runs as root here")
    script = render_join_script(forge_url="https://f", code="c")
    result = subprocess.run(["sh"], input=script.encode(), capture_output=True, cwd=tmp_path)
    assert result.returncode == 1
    assert b"run this as root" in result.stderr


def test_leave_runs_connector_hooks_before_removing_their_files():
    script = render_leave_script()
    hooks = script.index("for hook in /etc/niuu/leave.d/*.sh; do")
    assert 'if [ -e "$hook" ]; then sh -eu "$hook"; fi' in script
    assert hooks < script.index('for path in $SETUP_FILES; do rm -f "$path"; done')
    assert "rmdir /etc/niuu/leave.d" in script
