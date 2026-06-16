"""Tests for the shared host path mount policy."""

from pathlib import Path

import pytest

from volundr.domain.mount_policy import ensure_host_path_allowed, is_host_path_allowed


class TestIsHostPathAllowed:
    def test_empty_prefixes_allow_any_path(self, tmp_path: Path) -> None:
        assert is_host_path_allowed(str(tmp_path), []) is True

    def test_root_requires_explicit_opt_in(self) -> None:
        assert is_host_path_allowed("/", []) is False
        assert is_host_path_allowed("/", [], allow_root_mount=True) is True

    def test_path_under_allowed_prefix(self, tmp_path: Path) -> None:
        nested = tmp_path / "projects" / "repo"
        nested.mkdir(parents=True)

        assert is_host_path_allowed(str(nested), [str(tmp_path)]) is True

    def test_path_outside_allowed_prefix(self, tmp_path: Path) -> None:
        allowed = tmp_path / "allowed"
        outside = tmp_path / "outside"
        allowed.mkdir()
        outside.mkdir()

        assert is_host_path_allowed(str(outside), [str(allowed)]) is False

    def test_prefix_match_is_segment_aware(self, tmp_path: Path) -> None:
        allowed = tmp_path / "data"
        sneaky = tmp_path / "data-evil"
        allowed.mkdir()
        sneaky.mkdir()

        assert is_host_path_allowed(str(sneaky), [str(allowed)]) is False

    def test_symlink_escape_is_denied(self, tmp_path: Path) -> None:
        allowed = tmp_path / "allowed"
        outside = tmp_path / "outside"
        allowed.mkdir()
        outside.mkdir()
        escape = allowed / "escape"
        escape.symlink_to(outside)

        assert is_host_path_allowed(str(escape), [str(allowed)]) is False

    def test_symlinked_prefix_still_matches(self, tmp_path: Path) -> None:
        real = tmp_path / "real"
        real.mkdir()
        (real / "ws").mkdir()
        link = tmp_path / "link"
        link.symlink_to(real)

        assert is_host_path_allowed(str(link / "ws"), [str(real)]) is True


class TestEnsureHostPathAllowed:
    def test_allowed_path_passes(self, tmp_path: Path) -> None:
        ensure_host_path_allowed(str(tmp_path), [str(tmp_path)])

    def test_denied_path_raises(self, tmp_path: Path) -> None:
        allowed = tmp_path / "allowed"
        allowed.mkdir()

        with pytest.raises(ValueError, match="not under any allowed prefix"):
            ensure_host_path_allowed(str(tmp_path / "outside"), [str(allowed)])

    def test_root_raises_without_opt_in(self) -> None:
        with pytest.raises(ValueError, match="allow_root_mount"):
            ensure_host_path_allowed("/", [])
