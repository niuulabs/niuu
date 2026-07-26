"""Tests for persona/profile resolution by path and the strict CLI resolvers.

``--persona`` and ``--profile`` accept a registry name or a file path, and an
explicitly named one that does not resolve is an error rather than a silent
fallback to defaults.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from ravn.adapters.personas.loader import FilesystemPersonaAdapter
from ravn.cli.commands import (
    _looks_like_path,
    _require_persona,
    _require_profile,
    _resolve_persona,
    _resolve_profile,
    app,
)

runner = CliRunner()


class TestLooksLikePath:
    @pytest.mark.parametrize("name", ["reviewer", "coding-agent", "local"])
    def test_bare_names_are_registry_names(self, name: str) -> None:
        assert _looks_like_path(name) is False

    @pytest.mark.parametrize(
        "name", ["./reviewer.yaml", "/tmp/a.yaml", "dir/x.yml", "custom.yaml", "custom.yml"]
    )
    def test_path_shaped_names_are_paths(self, name: str) -> None:
        assert _looks_like_path(name) is True


class TestPersonaByPath:
    def test_path_load_matches_name_load(self, tmp_path: Path) -> None:
        """A persona addressed by path behaves exactly like one addressed by name."""
        loader = FilesystemPersonaAdapter()
        by_name = loader.load("reviewer")
        assert by_name is not None, "bundled 'reviewer' persona is expected to exist"

        source = Path(loader.source("reviewer"))
        copied = tmp_path / "custom-reviewer.yaml"
        copied.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

        by_path = _resolve_persona(str(copied), None)

        assert by_path is not None
        assert by_path.name == by_name.name
        assert by_path.system_prompt_template == by_name.system_prompt_template
        assert by_path.allowed_tools == by_name.allowed_tools

    def test_load_path_injects_outcome_instruction_like_load(self, tmp_path: Path) -> None:
        """load_path mirrors load — otherwise a file persona would lose its outcome block."""
        loader = FilesystemPersonaAdapter()
        source = Path(loader.source("reviewer"))
        copied = tmp_path / "copy.yaml"
        copied.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

        assert loader.load_path(copied) == loader.load("reviewer")

    def test_missing_file_resolves_to_none(self, tmp_path: Path) -> None:
        assert _resolve_persona(str(tmp_path / "absent.yaml"), None) is None

    def test_malformed_file_resolves_to_none(self, tmp_path: Path) -> None:
        broken = tmp_path / "broken.yaml"
        broken.write_text("::: not valid yaml :::", encoding="utf-8")

        assert _resolve_persona(str(broken), None) is None

    def test_warn_false_suppresses_the_fallback_warning(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _resolve_persona(str(tmp_path / "absent.yaml"), None, warn=False)

        assert capsys.readouterr().err == ""


class TestRequirePersona:
    def test_unresolvable_explicit_persona_exits(self) -> None:
        with pytest.raises(typer.Exit) as exc:
            _require_persona("does-not-exist", None)

        assert exc.value.exit_code == 2

    def test_empty_persona_stays_a_soft_none(self) -> None:
        assert _require_persona("", None) is None

    def test_resolvable_persona_is_returned(self) -> None:
        persona = _require_persona("reviewer", None)

        assert persona is not None
        assert persona.name == "reviewer"

    def test_error_is_reported_once(self, capsys: pytest.CaptureFixture[str]) -> None:
        """The operator sees an error, not an error preceded by a warning."""
        with pytest.raises(typer.Exit):
            _require_persona("does-not-exist", None)

        err = capsys.readouterr().err
        assert "Warning:" not in err
        assert "Error: persona 'does-not-exist' not found." in err

    def test_path_error_omits_the_registry_hint(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(typer.Exit):
            _require_persona(str(tmp_path / "absent.yaml"), None)

        assert "ravn personas list" not in capsys.readouterr().err


class TestProfileByPathAndRequire:
    def test_profile_loads_from_path(self, tmp_path: Path) -> None:
        profile_file = tmp_path / "custom.yaml"
        profile_file.write_text(
            "name: custom\npersona: reviewer\ndeployment: ephemeral\n", encoding="utf-8"
        )

        profile = _resolve_profile(str(profile_file))

        assert profile is not None
        assert profile.name == "custom"
        assert profile.persona == "reviewer"

    def test_builtin_profile_still_loads_by_name(self) -> None:
        profile = _resolve_profile("local")

        assert profile is not None
        assert profile.name == "local"

    def test_unresolvable_explicit_profile_exits(self) -> None:
        with pytest.raises(typer.Exit) as exc:
            _require_profile("does-not-exist")

        assert exc.value.exit_code == 2

    def test_empty_profile_stays_a_soft_none(self) -> None:
        assert _require_profile("") is None

    def test_missing_profile_file_resolves_to_none(self, tmp_path: Path) -> None:
        assert _resolve_profile(str(tmp_path / "absent.yaml")) is None


class TestRegistryListings:
    """Exercised through the top-level app — the surface an operator actually types."""

    def test_personas_list_includes_a_known_persona(self) -> None:
        result = runner.invoke(app, ["personas", "list"])

        assert result.exit_code == 0, result.output
        assert "reviewer" in result.output

    def test_personas_builtin_listing_is_a_subset(self) -> None:
        full = runner.invoke(app, ["personas", "list"])
        builtin = runner.invoke(app, ["personas", "list", "--builtin"])

        assert builtin.exit_code == 0, builtin.output
        builtin_names = {line.split()[0] for line in builtin.output.splitlines() if line.strip()}
        full_names = {line.split()[0] for line in full.output.splitlines() if line.strip()}
        assert builtin_names
        assert builtin_names <= full_names

    def test_profiles_list_reports_builtins(self) -> None:
        result = runner.invoke(app, ["profiles", "list"])

        assert result.exit_code == 0, result.output
        assert "local" in result.output
        assert "builtin" in result.output

    def test_profiles_builtin_only_listing(self) -> None:
        result = runner.invoke(app, ["profiles", "list", "--builtin"])

        assert result.exit_code == 0, result.output
        assert "local" in result.output

    def test_empty_persona_registry_is_an_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An empty registry means a broken install, not an empty success."""
        monkeypatch.setattr(FilesystemPersonaAdapter, "list_names", lambda self: [])

        result = runner.invoke(app, ["personas", "list"])

        assert result.exit_code == 1
        assert "No personas found" in result.output

    def test_empty_profile_registry_is_an_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from ravn.adapters.profiles.loader import ProfileLoader

        monkeypatch.setattr(ProfileLoader, "list_names", lambda self: [])

        result = runner.invoke(app, ["profiles", "list"])

        assert result.exit_code == 1
        assert "No profiles found" in result.output

    def test_room_subcommands_are_mounted(self) -> None:
        """`room` moved from an action argument to a sub-app; the verbs must survive."""
        result = runner.invoke(app, ["room", "--help"])

        assert result.exit_code == 0, result.output
        for verb in ("create", "ls", "show", "start", "stop", "rm", "join", "participants"):
            assert verb in result.output
