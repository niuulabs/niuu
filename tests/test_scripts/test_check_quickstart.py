"""Checks for the executable documentation contract and failure reporting."""

from unittest.mock import Mock

import pytest

from scripts import check_quickstart


@pytest.mark.parametrize("name", ["version", "init", "start", "health", "authenticate"])
def test_documented_commands_have_one_executable_block(name):
    assert check_quickstart.command(name).strip()


@pytest.mark.parametrize("copies", [0, 2])
def test_missing_or_duplicate_commands_fail(tmp_path, monkeypatch, copies):
    guide = tmp_path / "guide.md"
    guide.write_text("<!-- quickstart-check: start -->\n```bash\nniuu platform up\n```\n" * copies)
    monkeypatch.setattr(check_quickstart, "GUIDE", guide)
    with pytest.raises(ValueError, match="Expected one"):
        check_quickstart.command("start")


def test_platform_crash_is_not_reported_as_a_successful_wait():
    process = Mock()
    process.poll.return_value = 1
    with pytest.raises(RuntimeError, match="Platform exited"):
        check_quickstart.wait_until(lambda: True, process, 1, "running session")


def test_wait_is_bounded():
    process = Mock()
    process.poll.return_value = None
    with pytest.raises(TimeoutError, match="hello.txt"):
        check_quickstart.wait_until(lambda: False, process, 0, "hello.txt")


def test_documented_bash_blocks_have_valid_shell_syntax():
    import re
    import subprocess

    for page in (check_quickstart.ROOT / "docs/site").rglob("*.md"):
        for block in re.findall(r"```bash\n(.*?)\n```", page.read_text(), re.DOTALL):
            result = subprocess.run(
                ["bash", "-n"], input=block, text=True, capture_output=True, check=False
            )
            assert result.returncode == 0, f"{page}: {result.stderr}"
