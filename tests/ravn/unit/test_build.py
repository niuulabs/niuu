"""Unit tests for ravn.build — platform_suffix and build_command."""

from __future__ import annotations

import sys
from unittest.mock import patch

from ravn.build import build_command, platform_suffix


class TestPlatformSuffix:
    def test_returns_string(self) -> None:
        result = platform_suffix()
        assert isinstance(result, str)
        assert "-" in result

    def test_linux_amd64(self) -> None:
        with patch("platform.system", return_value="Linux"):
            with patch("platform.machine", return_value="x86_64"):
                result = platform_suffix()
        assert result == "linux-amd64"

    def test_darwin_arm64(self) -> None:
        with patch("platform.system", return_value="Darwin"):
            with patch("platform.machine", return_value="aarch64"):
                result = platform_suffix()
        assert result == "darwin-arm64"

    def test_unknown_arch_passthrough(self) -> None:
        with patch("platform.system", return_value="Linux"):
            with patch("platform.machine", return_value="mips"):
                result = platform_suffix()
        assert result == "linux-mips"


class TestBuildCommand:
    def test_returns_list_of_strings(self) -> None:
        cmd = build_command()
        assert isinstance(cmd, list)
        assert all(isinstance(s, str) for s in cmd)

    def test_starts_with_python(self) -> None:
        cmd = build_command()
        assert cmd[0] == sys.executable

    def test_includes_nuitka(self) -> None:
        cmd = build_command()
        assert "-m" in cmd
        assert "nuitka" in cmd

    def test_includes_onefile_flag(self) -> None:
        cmd = build_command()
        assert "--onefile" in cmd

    def test_custom_binary_name_in_output_filename(self) -> None:
        cmd = build_command(binary_name="my-ravn")
        # The --output-filename flag should contain our binary name
        output_flags = [f for f in cmd if f.startswith("--output-filename=")]
        assert len(output_flags) == 1
        assert "my-ravn" in output_flags[0]

    def test_custom_output_dir(self) -> None:
        cmd = build_command(output_dir="/tmp/custom")
        dir_flags = [f for f in cmd if f.startswith("--output-dir=")]
        assert len(dir_flags) == 1
        assert "/tmp/custom" in dir_flags[0]

    def test_custom_entry_point(self) -> None:
        cmd = build_command(entry_point="/path/to/entry.py")
        assert "/path/to/entry.py" in cmd

    def test_includes_core_packages(self) -> None:
        cmd = build_command()
        # ravn and bifrost should always be included
        package_flags = [f for f in cmd if f.startswith("--include-package=")]
        packages = [f.split("=", 1)[1] for f in package_flags]
        assert "ravn" in packages
        assert "bifrost" in packages

    def test_includes_nofollow_imports(self) -> None:
        cmd = build_command()
        nofollow_flags = [f for f in cmd if f.startswith("--nofollow-import-to=")]
        assert len(nofollow_flags) == 1
        assert "pytest" in nofollow_flags[0]


class TestModuleImports:
    def test_memory_common_importable(self) -> None:
        """Importing memory.common exports CHARS_PER_TOKEN (covers lines 3, 6)."""
        from ravn.adapters.memory.common import CHARS_PER_TOKEN

        assert isinstance(CHARS_PER_TOKEN, int)
        assert CHARS_PER_TOKEN > 0
