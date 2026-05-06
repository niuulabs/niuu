"""Tests for MountedVolumePersonaAdapter."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from ravn.adapters.personas.mounted_volume import (
    _NOT_IMPLEMENTED_MSG,
    MountedVolumePersonaAdapter,
)
from ravn.ports.persona import PersonaPort

# ---------------------------------------------------------------------------
# YAML fixtures
# ---------------------------------------------------------------------------

_PERSONA_A_YAML = """\
name: agent-a
system_prompt_template: |
  You are agent A.
allowed_tools: [file, git]
permission_mode: workspace-write
"""

_PERSONA_B_YAML = """\
name: agent-b
system_prompt_template: You are agent B.
allowed_tools: [web]
"""

_PERSONA_A_OVERRIDE_YAML = """\
name: agent-a
system_prompt_template: |
  You are the overridden agent A.
allowed_tools: [cascade]
permission_mode: read-only
"""

_MALFORMED_YAML = """\
: this is not valid yaml: {[unclosed bracket
"""

_EMPTY_YAML = """\
"""

_NOT_A_DICT_YAML = """\
- just a list
- of items
"""

_NO_NAME_YAML = """\
system_prompt_template: persona without a name field
allowed_tools: [file]
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(directory: Path, filename: str, content: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Port contract
# ---------------------------------------------------------------------------


class TestPortContract:
    def test_is_persona_port(self, tmp_path: Path) -> None:
        adapter = MountedVolumePersonaAdapter(mount_path=str(tmp_path))
        assert isinstance(adapter, PersonaPort)


# ---------------------------------------------------------------------------
# Happy path — basic load / list / load_all / source / is_builtin
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_load_existing_persona(self, tmp_path: Path) -> None:
        _write(tmp_path, "agent-a.yaml", _PERSONA_A_YAML)
        adapter = MountedVolumePersonaAdapter(mount_path=str(tmp_path))
        persona = adapter.load("agent-a")
        assert persona is not None
        assert persona.name == "agent-a"
        assert "agent A" in persona.system_prompt_template
        assert persona.allowed_tools == ["file", "git"]

    def test_load_returns_none_for_missing_name(self, tmp_path: Path) -> None:
        adapter = MountedVolumePersonaAdapter(mount_path=str(tmp_path))
        assert adapter.load("nonexistent") is None

    def test_list_names_returns_sorted(self, tmp_path: Path) -> None:
        _write(tmp_path, "agent-b.yaml", _PERSONA_B_YAML)
        _write(tmp_path, "agent-a.yaml", _PERSONA_A_YAML)
        adapter = MountedVolumePersonaAdapter(mount_path=str(tmp_path))
        assert adapter.list_names() == ["agent-a", "agent-b"]

    def test_load_all_returns_all_parseable(self, tmp_path: Path) -> None:
        _write(tmp_path, "agent-a.yaml", _PERSONA_A_YAML)
        _write(tmp_path, "agent-b.yaml", _PERSONA_B_YAML)
        adapter = MountedVolumePersonaAdapter(mount_path=str(tmp_path))
        personas = adapter.load_all()
        names = {p.name for p in personas}
        assert names == {"agent-a", "agent-b"}

    def test_source_returns_file_path(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "agent-a.yaml", _PERSONA_A_YAML)
        adapter = MountedVolumePersonaAdapter(mount_path=str(tmp_path))
        assert adapter.source("agent-a") == str(path)

    def test_source_returns_empty_string_for_unknown(self, tmp_path: Path) -> None:
        adapter = MountedVolumePersonaAdapter(mount_path=str(tmp_path))
        assert adapter.source("ghost") == ""

    def test_is_builtin_always_false(self, tmp_path: Path) -> None:
        _write(tmp_path, "agent-a.yaml", _PERSONA_A_YAML)
        adapter = MountedVolumePersonaAdapter(mount_path=str(tmp_path))
        assert adapter.is_builtin("agent-a") is False
        assert adapter.is_builtin("nonexistent") is False


# ---------------------------------------------------------------------------
# Bootstrap case — mount_path does not exist yet
# ---------------------------------------------------------------------------


class TestMissingMountPath:
    def test_load_returns_none_when_path_missing(self, tmp_path: Path) -> None:
        absent = tmp_path / "not-created-yet"
        adapter = MountedVolumePersonaAdapter(mount_path=str(absent))
        assert adapter.load("anything") is None

    def test_list_names_empty_when_path_missing(self, tmp_path: Path) -> None:
        absent = tmp_path / "not-created-yet"
        adapter = MountedVolumePersonaAdapter(mount_path=str(absent))
        assert adapter.list_names() == []

    def test_load_all_empty_when_path_missing(self, tmp_path: Path) -> None:
        absent = tmp_path / "not-created-yet"
        adapter = MountedVolumePersonaAdapter(mount_path=str(absent))
        assert adapter.load_all() == []


# ---------------------------------------------------------------------------
# Overlay ordering — later entry wins by name
# ---------------------------------------------------------------------------


class TestOverlayOrdering:
    def test_later_overlay_overrides_earlier_by_name(self, tmp_path: Path) -> None:
        base_dir = tmp_path / "base"
        override_dir = tmp_path / "override"
        _write(base_dir, "agent-a.yaml", _PERSONA_A_YAML)
        _write(override_dir, "agent-a.yaml", _PERSONA_A_OVERRIDE_YAML)

        adapter = MountedVolumePersonaAdapter(
            mount_path=str(base_dir),
            overlay_paths=[str(override_dir)],
        )
        persona = adapter.load("agent-a")
        assert persona is not None
        assert "overridden" in persona.system_prompt_template
        assert persona.allowed_tools == ["cascade"]
        assert persona.permission_mode == "read-only"

    def test_overlay_does_not_affect_non_overlapping_names(self, tmp_path: Path) -> None:
        base_dir = tmp_path / "base"
        overlay_dir = tmp_path / "overlay"
        _write(base_dir, "agent-a.yaml", _PERSONA_A_YAML)
        _write(overlay_dir, "agent-b.yaml", _PERSONA_B_YAML)

        adapter = MountedVolumePersonaAdapter(
            mount_path=str(base_dir),
            overlay_paths=[str(overlay_dir)],
        )
        assert adapter.list_names() == ["agent-a", "agent-b"]
        assert adapter.load("agent-a") is not None
        assert adapter.load("agent-b") is not None

    def test_three_layer_overlay_last_wins(self, tmp_path: Path) -> None:
        layer1 = tmp_path / "layer1"
        layer2 = tmp_path / "layer2"
        layer3 = tmp_path / "layer3"

        _write(layer1, "shared.yaml", "name: shared\nsystem_prompt_template: layer1\n")
        _write(layer2, "shared.yaml", "name: shared\nsystem_prompt_template: layer2\n")
        _write(layer3, "shared.yaml", "name: shared\nsystem_prompt_template: layer3\n")

        adapter = MountedVolumePersonaAdapter(
            mount_path=str(layer1),
            overlay_paths=[str(layer2), str(layer3)],
        )
        persona = adapter.load("shared")
        assert persona is not None
        assert "layer3" in persona.system_prompt_template

    def test_missing_overlay_path_is_silently_skipped(self, tmp_path: Path) -> None:
        base_dir = tmp_path / "base"
        absent_overlay = tmp_path / "does-not-exist"
        _write(base_dir, "agent-a.yaml", _PERSONA_A_YAML)

        adapter = MountedVolumePersonaAdapter(
            mount_path=str(base_dir),
            overlay_paths=[str(absent_overlay)],
        )
        # Should not raise; base persona still accessible
        assert adapter.load("agent-a") is not None
        assert adapter.list_names() == ["agent-a"]


# ---------------------------------------------------------------------------
# Symlink following — k8s projected ConfigMap ..data indirection
# ---------------------------------------------------------------------------


class TestSymlinkFollowing:
    def test_follows_symlink_to_real_directory(self, tmp_path: Path) -> None:
        real_dir = tmp_path / "real_data"
        _write(real_dir, "agent-a.yaml", _PERSONA_A_YAML)

        # Mimic k8s projected ConfigMap: mount_path/..data -> real_dir
        dotdata = tmp_path / "..data"
        dotdata.symlink_to(real_dir)

        # The mount path itself is the symlink target (..data IS the dir)
        adapter = MountedVolumePersonaAdapter(mount_path=str(dotdata))
        persona = adapter.load("agent-a")
        assert persona is not None
        assert persona.name == "agent-a"

    def test_follows_symlinked_yaml_file(self, tmp_path: Path) -> None:
        real_dir = tmp_path / "real"
        link_dir = tmp_path / "link"
        _write(real_dir, "agent-a.yaml", _PERSONA_A_YAML)
        link_dir.mkdir()
        # Symlink the yaml file itself
        (link_dir / "agent-a.yaml").symlink_to(real_dir / "agent-a.yaml")

        adapter = MountedVolumePersonaAdapter(mount_path=str(link_dir))
        persona = adapter.load("agent-a")
        assert persona is not None
        assert persona.name == "agent-a"

    def test_k8s_configmap_structure(self, tmp_path: Path) -> None:
        """Simulate full k8s projected ConfigMap layout with ..data symlink."""
        # Real files live in a timestamped directory (k8s internals)
        versioned = tmp_path / "..2026_04_18_12_00_00.987654321"
        _write(versioned, "reviewer.yaml", "name: reviewer\nsystem_prompt_template: review\n")
        _write(versioned, "coder.yaml", "name: coder\nsystem_prompt_template: code\n")

        # ..data points to the versioned directory
        dotdata = tmp_path / "..data"
        dotdata.symlink_to(versioned)

        adapter = MountedVolumePersonaAdapter(mount_path=str(dotdata))
        names = adapter.list_names()
        assert "reviewer" in names
        assert "coder" in names
        assert adapter.load("reviewer") is not None
        assert adapter.load("coder") is not None


# ---------------------------------------------------------------------------
# Malformed / invalid files — skipped with WARN, others still load
# ---------------------------------------------------------------------------


class TestMalformedFiles:
    def test_malformed_yaml_is_skipped(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        _write(tmp_path, "bad.yaml", _MALFORMED_YAML)
        _write(tmp_path, "agent-a.yaml", _PERSONA_A_YAML)

        adapter = MountedVolumePersonaAdapter(mount_path=str(tmp_path))
        with caplog.at_level(logging.WARNING, logger="ravn.adapters.personas.mounted_volume"):
            personas = adapter.load_all()

        names = {p.name for p in personas}
        assert "agent-a" in names
        assert "bad" not in names
        assert any("bad.yaml" in r.message or "bad" in r.message for r in caplog.records)

    def test_empty_yaml_file_is_skipped(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        _write(tmp_path, "empty.yaml", _EMPTY_YAML)
        _write(tmp_path, "agent-b.yaml", _PERSONA_B_YAML)

        adapter = MountedVolumePersonaAdapter(mount_path=str(tmp_path))
        with caplog.at_level(logging.WARNING, logger="ravn.adapters.personas.mounted_volume"):
            personas = adapter.load_all()

        names = {p.name for p in personas}
        assert "agent-b" in names
        assert len([p for p in personas if p.name == "empty"]) == 0

    def test_non_dict_yaml_is_skipped(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        _write(tmp_path, "list.yaml", _NOT_A_DICT_YAML)
        _write(tmp_path, "agent-a.yaml", _PERSONA_A_YAML)

        adapter = MountedVolumePersonaAdapter(mount_path=str(tmp_path))
        with caplog.at_level(logging.WARNING, logger="ravn.adapters.personas.mounted_volume"):
            personas = adapter.load_all()

        names = {p.name for p in personas}
        assert "agent-a" in names
        assert "list" not in names

    def test_yaml_without_name_field_is_skipped(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        _write(tmp_path, "noname.yaml", _NO_NAME_YAML)
        _write(tmp_path, "agent-a.yaml", _PERSONA_A_YAML)

        adapter = MountedVolumePersonaAdapter(mount_path=str(tmp_path))
        with caplog.at_level(logging.WARNING, logger="ravn.adapters.personas.mounted_volume"):
            personas = adapter.load_all()

        names = {p.name for p in personas}
        assert "agent-a" in names
        assert "noname" not in names

    def test_malformed_load_returns_none(self, tmp_path: Path) -> None:
        _write(tmp_path, "bad.yaml", _MALFORMED_YAML)
        adapter = MountedVolumePersonaAdapter(mount_path=str(tmp_path))
        # list_names sees "bad" but load returns None due to parse failure
        assert "bad" in adapter.list_names()
        assert adapter.load("bad") is None

    def test_multiple_malformed_files_others_load(self, tmp_path: Path) -> None:
        _write(tmp_path, "bad1.yaml", _MALFORMED_YAML)
        _write(tmp_path, "bad2.yaml", _NOT_A_DICT_YAML)
        _write(tmp_path, "agent-a.yaml", _PERSONA_A_YAML)
        _write(tmp_path, "agent-b.yaml", _PERSONA_B_YAML)

        adapter = MountedVolumePersonaAdapter(mount_path=str(tmp_path))
        personas = adapter.load_all()
        names = {p.name for p in personas}
        assert names == {"agent-a", "agent-b"}


# ---------------------------------------------------------------------------
# Write operations — must raise NotImplementedError
# ---------------------------------------------------------------------------


class TestWriteOperationsNotImplemented:
    def test_save_raises_not_implemented(self, tmp_path: Path) -> None:
        from ravn.adapters.personas.loader import PersonaConfig

        adapter = MountedVolumePersonaAdapter(mount_path=str(tmp_path))
        config = PersonaConfig(name="dummy")
        with pytest.raises(NotImplementedError, match=_NOT_IMPLEMENTED_MSG):
            adapter.save(config)

    def test_delete_raises_not_implemented(self, tmp_path: Path) -> None:
        adapter = MountedVolumePersonaAdapter(mount_path=str(tmp_path))
        with pytest.raises(NotImplementedError, match=_NOT_IMPLEMENTED_MSG):
            adapter.delete("dummy")

    def test_not_implemented_message_content(self, tmp_path: Path) -> None:
        from ravn.adapters.personas.loader import PersonaConfig

        adapter = MountedVolumePersonaAdapter(mount_path=str(tmp_path))
        with pytest.raises(NotImplementedError) as exc_info:
            adapter.save(PersonaConfig(name="dummy"))
        assert "volundr REST API" in str(exc_info.value)
        assert "ConfigMap" in str(exc_info.value)
