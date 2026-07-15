"""Unit tests for file security utilities."""

from __future__ import annotations

from pathlib import Path

import pytest

from ravn.adapters.tools.file_security import (
    DEFAULT_BINARY_CHECK_BYTES,
    PathSecurityError,
    is_binary,
    resolve_safe,
)

# ---------------------------------------------------------------------------
# resolve_safe — workspace boundary
# ---------------------------------------------------------------------------


def test_resolve_path_inside_workspace(tmp_path: Path):
    target = tmp_path / "subdir" / "file.txt"
    result = resolve_safe(target, tmp_path)
    assert result == target.resolve()


def test_resolve_workspace_root_itself(tmp_path: Path):
    result = resolve_safe(tmp_path, tmp_path)
    assert result == tmp_path.resolve()


def test_resolve_rejects_dotdot_traversal(tmp_path: Path):
    with pytest.raises(PathSecurityError, match="outside the workspace"):
        resolve_safe(tmp_path / ".." / "sibling.txt", tmp_path)


def test_resolve_rejects_absolute_path_outside(tmp_path: Path):
    with pytest.raises(PathSecurityError, match="outside the workspace"):
        resolve_safe("/tmp/attacker.txt", tmp_path)


def test_resolve_string_path(tmp_path: Path):
    target = str(tmp_path / "file.txt")
    result = resolve_safe(target, tmp_path)
    assert result == Path(target).resolve()


def test_resolve_relative_path_from_workspace(tmp_path: Path):
    result = resolve_safe("subdir/file.txt", tmp_path)
    assert result == (tmp_path / "subdir" / "file.txt").resolve()


def test_resolve_nonexistent_target_is_ok(tmp_path: Path):
    """Write targets do not need to exist."""
    result = resolve_safe(tmp_path / "new_file.txt", tmp_path)
    assert result == (tmp_path / "new_file.txt").resolve()


# ---------------------------------------------------------------------------
# resolve_safe — symlink validation
# ---------------------------------------------------------------------------


def test_resolve_rejects_symlink_pointing_outside(tmp_path: Path):
    """A symlink inside the workspace that points outside must be rejected."""
    link = tmp_path / "evil_link"
    # Point to parent directory (outside workspace)
    link.symlink_to(tmp_path.parent)
    with pytest.raises(PathSecurityError):
        resolve_safe(link / "some_file.txt", tmp_path)


def test_resolve_rejects_symlink_to_etc(tmp_path: Path):
    link = tmp_path / "etc_link"
    link.symlink_to("/etc")
    with pytest.raises(PathSecurityError):
        resolve_safe(link, tmp_path)


def test_resolve_allows_internal_symlink(tmp_path: Path):
    """Symlink within the workspace should be allowed."""
    real_file = tmp_path / "real.txt"
    real_file.write_text("hello")
    link = tmp_path / "link.txt"
    link.symlink_to(real_file)
    result = resolve_safe(link, tmp_path)
    assert result == real_file.resolve()


# ---------------------------------------------------------------------------
# resolve_safe — system path rejection
# ---------------------------------------------------------------------------


def test_resolve_rejects_etc(tmp_path: Path):
    # Use root as workspace to bypass workspace boundary check
    root = Path("/")
    with pytest.raises(PathSecurityError, match="system path"):
        resolve_safe("/etc/passwd", root)


def test_resolve_rejects_usr(tmp_path: Path):
    root = Path("/")
    with pytest.raises(PathSecurityError, match="system path"):
        resolve_safe("/usr/bin/env", root)


def test_resolve_rejects_var(tmp_path: Path):
    root = Path("/")
    with pytest.raises(PathSecurityError, match="system path"):
        resolve_safe("/var/log/syslog", root)


def test_resolve_rejects_boot(tmp_path: Path):
    root = Path("/")
    with pytest.raises(PathSecurityError, match="system path"):
        resolve_safe("/boot/grub/grub.cfg", root)


def test_resolve_rejects_sys(tmp_path: Path):
    root = Path("/")
    with pytest.raises(PathSecurityError, match="system path"):
        resolve_safe("/sys/fs/cgroup", root)


def test_resolve_rejects_proc(tmp_path: Path):
    root = Path("/")
    with pytest.raises(PathSecurityError, match="system path"):
        resolve_safe("/proc/1/mem", root)


def test_resolve_rejects_exact_system_prefix(tmp_path: Path):
    root = Path("/")
    with pytest.raises(PathSecurityError, match="system path"):
        resolve_safe("/etc", root)


# ---------------------------------------------------------------------------
# is_binary
# ---------------------------------------------------------------------------


def test_is_binary_detects_nul_byte():
    assert is_binary(b"hello\x00world")


def test_is_binary_returns_false_for_text():
    assert not is_binary(b"hello world\n")


def test_is_binary_empty_data():
    assert not is_binary(b"")


def test_is_binary_nul_within_check_window():
    data = b"a" * 100 + b"\x00" + b"a" * 1000
    assert is_binary(data)


def test_is_binary_nul_beyond_check_limit():
    """NUL bytes beyond the check window should not trigger detection."""
    data = b"a" * DEFAULT_BINARY_CHECK_BYTES + b"\x00"
    assert not is_binary(data)


def test_is_binary_custom_check_bytes():
    data = b"a" * 5 + b"\x00" + b"a" * 100
    assert is_binary(data, check_bytes=10)
    assert not is_binary(data, check_bytes=3)


def test_is_binary_all_nul():
    assert is_binary(b"\x00" * 100)


def test_is_binary_utf8_text():
    text = "héllo wörld\n".encode()
    assert not is_binary(text)
