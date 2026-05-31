"""Tests for PersonaRegistryPort and multi-directory FilesystemPersonaAdapter."""

from __future__ import annotations

from pathlib import Path

from ravn.adapters.personas.loader import (
    _BUILTIN_PERSONAS_DIR,
    FilesystemPersonaAdapter,
    PersonaConfig,
    PersonaConsumes,
    PersonaFanIn,
    PersonaLLMConfig,
    PersonaProduces,
)
from ravn.ports.persona import PersonaPort, PersonaRegistryPort

_BUILTIN_NAMES = sorted(p.stem for p in _BUILTIN_PERSONAS_DIR.glob("*.yaml"))

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_SIMPLE_PERSONA_YAML = """\
name: custom-agent
system_prompt_template: |
  You are a custom agent.
allowed_tools: [file, git]
forbidden_tools: [cascade]
permission_mode: workspace-write
llm:
  primary_alias: balanced
  thinking_enabled: false
iteration_budget: 10
"""

_OTHER_PERSONA_YAML = """\
name: other-agent
system_prompt_template: You are other.
allowed_tools: [web]
permission_mode: read-only
"""


def _write_persona(directory: Path, name: str, content: str) -> Path:
    """Write a persona YAML file and return the path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.yaml"
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Port contract
# ---------------------------------------------------------------------------


class TestPortContracts:
    def test_persona_loader_is_persona_port(self) -> None:
        loader = FilesystemPersonaAdapter()
        assert isinstance(loader, PersonaPort)

    def test_persona_loader_is_registry_port(self) -> None:
        loader = FilesystemPersonaAdapter()
        assert isinstance(loader, PersonaRegistryPort)

    def test_persona_registry_port_is_abstract(self) -> None:
        import inspect

        assert inspect.isabstract(PersonaRegistryPort)

    def test_persona_registry_port_abstract_methods(self) -> None:
        import inspect

        abstract = {
            name
            for name, _ in inspect.getmembers(PersonaRegistryPort, predicate=inspect.isfunction)
            if getattr(getattr(PersonaRegistryPort, name), "__isabstractmethod__", False)
        }
        assert "save" in abstract
        assert "delete" in abstract
        assert "is_builtin" in abstract
        assert "load_all" in abstract
        assert "source" in abstract


# ---------------------------------------------------------------------------
# Constructor / _resolve_dirs
# ---------------------------------------------------------------------------


class TestConstructor:
    def test_default_no_args(self, tmp_path: Path) -> None:
        """Default constructor sets dirs to project-local + user-global."""
        loader = FilesystemPersonaAdapter(cwd=tmp_path)
        dirs = loader._resolve_dirs()
        assert dirs[0] == tmp_path / ".ravn" / "personas"
        assert dirs[1] == Path.home() / ".ravn" / "personas"

    def test_explicit_persona_dirs(self, tmp_path: Path) -> None:
        """Explicit persona_dirs replaces default discovery dirs."""
        custom = tmp_path / "custom"
        loader = FilesystemPersonaAdapter([str(custom)])
        dirs = loader._resolve_dirs()
        assert dirs[0] == custom
        # Builtin dir appended when include_builtin=True (default)
        assert _BUILTIN_PERSONAS_DIR in dirs

    def test_empty_persona_dirs_list_uses_explicit_empty(self, tmp_path: Path) -> None:
        """Passing an empty list means only bundled dir (when include_builtin)."""
        loader = FilesystemPersonaAdapter([])
        dirs = loader._resolve_dirs()
        assert dirs == [_BUILTIN_PERSONAS_DIR]

    def test_empty_persona_dirs_no_builtin(self, tmp_path: Path) -> None:
        """Passing an empty list with include_builtin=False means no dirs."""
        loader = FilesystemPersonaAdapter([], include_builtin=False)
        dirs = loader._resolve_dirs()
        assert dirs == []

    def test_expanduser_in_persona_dirs(self, tmp_path: Path) -> None:
        """~ is expanded in persona_dirs entries."""
        loader = FilesystemPersonaAdapter(["~/some/dir"])
        dirs = loader._resolve_dirs()
        assert dirs[0] == Path.home() / "some" / "dir"

    def test_include_builtin_default_true(self) -> None:
        loader = FilesystemPersonaAdapter()
        assert loader._include_builtin is True

    def test_include_builtin_false(self) -> None:
        loader = FilesystemPersonaAdapter(include_builtin=False)
        assert loader._include_builtin is False


# ---------------------------------------------------------------------------
# list_names — multi-directory discovery
# ---------------------------------------------------------------------------


class TestListNames:
    def test_includes_builtin_names_by_default(self) -> None:
        loader = FilesystemPersonaAdapter()
        names = loader.list_names()
        for builtin in _BUILTIN_NAMES:
            assert builtin in names

    def test_includes_file_names(self, tmp_path: Path) -> None:
        _write_persona(tmp_path, "custom-agent", _SIMPLE_PERSONA_YAML)
        loader = FilesystemPersonaAdapter([str(tmp_path)])
        names = loader.list_names()
        assert "custom-agent" in names

    def test_union_of_all_dirs(self, tmp_path: Path) -> None:
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_persona(dir_a, "alpha", _SIMPLE_PERSONA_YAML.replace("custom-agent", "alpha"))
        _write_persona(dir_b, "beta", _SIMPLE_PERSONA_YAML.replace("custom-agent", "beta"))
        loader = FilesystemPersonaAdapter([str(dir_a), str(dir_b)])
        names = loader.list_names()
        assert "alpha" in names
        assert "beta" in names

    def test_deduplicates_overlapping_names(self, tmp_path: Path) -> None:
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_persona(dir_a, "shared", _SIMPLE_PERSONA_YAML.replace("custom-agent", "shared"))
        _write_persona(dir_b, "shared", _OTHER_PERSONA_YAML.replace("other-agent", "shared"))
        loader = FilesystemPersonaAdapter([str(dir_a), str(dir_b)])
        names = loader.list_names()
        assert names.count("shared") == 1

    def test_missing_directory_is_silently_skipped(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist"
        loader = FilesystemPersonaAdapter([str(missing)])
        names = loader.list_names()
        # no crash; built-ins still included since include_builtin defaults True
        assert isinstance(names, list)

    def test_no_builtin_when_include_builtin_false(self, tmp_path: Path) -> None:
        loader = FilesystemPersonaAdapter([], include_builtin=False)
        names = loader.list_names()
        for builtin in _BUILTIN_NAMES:
            assert builtin not in names

    def test_names_are_sorted(self, tmp_path: Path) -> None:
        loader = FilesystemPersonaAdapter(cwd=tmp_path)
        names = loader.list_names()
        assert names == sorted(names)


# ---------------------------------------------------------------------------
# load — multi-directory priority
# ---------------------------------------------------------------------------


class TestLoad:
    def test_project_local_wins_over_user_global(self, tmp_path: Path) -> None:
        """Project-local persona takes precedence over user-global."""
        project_local = tmp_path / "project" / ".ravn" / "personas"
        user_global = tmp_path / "home" / ".ravn" / "personas"

        project_yaml = _SIMPLE_PERSONA_YAML.replace("You are a custom agent.", "PROJECT version")
        user_yaml = _SIMPLE_PERSONA_YAML.replace("You are a custom agent.", "USER version")
        _write_persona(project_local, "custom-agent", project_yaml)
        _write_persona(user_global, "custom-agent", user_yaml)

        loader = FilesystemPersonaAdapter(
            [str(project_local), str(user_global)],
        )
        persona = loader.load("custom-agent")
        assert persona is not None
        assert "PROJECT version" in persona.system_prompt_template

    def test_first_dir_wins_on_conflict(self, tmp_path: Path) -> None:
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_persona(dir_a, "agent", _SIMPLE_PERSONA_YAML.replace("custom-agent", "agent"))
        _write_persona(
            dir_b,
            "agent",
            _SIMPLE_PERSONA_YAML.replace("custom-agent", "agent").replace(
                "You are a custom agent.", "SECOND"
            ),
        )
        loader = FilesystemPersonaAdapter([str(dir_a), str(dir_b)])
        persona = loader.load("agent")
        assert persona is not None
        # dir_a (index 0) has higher priority
        assert "You are a custom agent." in persona.system_prompt_template

    def test_falls_back_to_builtin(self, tmp_path: Path) -> None:
        loader = FilesystemPersonaAdapter([str(tmp_path / "empty")])
        persona = loader.load("coding-agent")
        assert persona is not None
        assert persona.name == "coding-agent"

    def test_returns_none_for_unknown_name(self, tmp_path: Path) -> None:
        loader = FilesystemPersonaAdapter([str(tmp_path)])
        assert loader.load("nonexistent-persona-xyz") is None

    def test_outcome_instruction_injected_for_builtin_with_schema(self) -> None:
        loader = FilesystemPersonaAdapter()
        persona = loader.load("reviewer")
        assert persona is not None
        assert "outcome" in persona.system_prompt_template.lower()

    def test_no_injection_when_no_schema(self, tmp_path: Path) -> None:
        _write_persona(tmp_path, "custom-agent", _SIMPLE_PERSONA_YAML)
        loader = FilesystemPersonaAdapter([str(tmp_path)])
        persona = loader.load("custom-agent")
        assert persona is not None
        # no produces schema → no injection marker
        assert "```outcome" not in persona.system_prompt_template

    def test_default_constructor_discovers_project_local(self, tmp_path: Path) -> None:
        """Default (no args) discovers .ravn/personas/ relative to cwd."""
        project_personas = tmp_path / ".ravn" / "personas"
        yaml = _SIMPLE_PERSONA_YAML.replace("custom-agent", "proj-persona")
        _write_persona(project_personas, "proj-persona", yaml)
        loader = FilesystemPersonaAdapter(cwd=tmp_path)
        persona = loader.load("proj-persona")
        assert persona is not None
        assert persona.name == "proj-persona"


# ---------------------------------------------------------------------------
# save / load round-trip
# ---------------------------------------------------------------------------


class TestSave:
    def test_save_writes_yaml_file(self, tmp_path: Path) -> None:
        config = PersonaConfig(
            name="my-agent",
            system_prompt_template="You are my agent.",
            allowed_tools=["file"],
            permission_mode="read-only",
            iteration_budget=5,
        )
        dest_dir = tmp_path / ".ravn" / "personas"

        # Patch Path.home() so save() writes to tmp_path
        import unittest.mock as mock

        with mock.patch("ravn.adapters.personas.loader.Path") as mock_path_cls:
            # We need Path to work normally but .home() returns tmp_path
            real_path = Path
            mock_path_cls.side_effect = real_path
            mock_path_cls.home.return_value = tmp_path
            mock_path_cls.cwd = real_path.cwd

            loader = FilesystemPersonaAdapter()
            loader.save(config)

        assert dest_dir.is_dir()
        yaml_file = dest_dir / "my-agent.yaml"
        assert yaml_file.is_file()

    def test_save_load_round_trip(self, tmp_path: Path) -> None:
        config = PersonaConfig(
            name="round-trip",
            system_prompt_template="Round trip test.",
            allowed_tools=["file", "git"],
            forbidden_tools=["cascade"],
            permission_mode="workspace-write",
            llm=PersonaLLMConfig(primary_alias="balanced", thinking_enabled=True),
            iteration_budget=20,
        )
        dest_dir = tmp_path / ".ravn" / "personas"
        dest_dir.mkdir(parents=True, exist_ok=True)

        import unittest.mock as mock

        with mock.patch("ravn.adapters.personas.loader.Path") as mock_path_cls:
            real_path = Path
            mock_path_cls.side_effect = real_path
            mock_path_cls.home.return_value = tmp_path
            mock_path_cls.cwd = real_path.cwd

            loader = FilesystemPersonaAdapter()
            loader.save(config)

        # Load it back from the written file
        loader2 = FilesystemPersonaAdapter([str(dest_dir)])
        loaded = loader2.load("round-trip")
        assert loaded is not None
        assert loaded.name == "round-trip"
        assert loaded.system_prompt_template == "Round trip test."
        assert loaded.allowed_tools == ["file", "git"]
        assert loaded.permission_mode == "workspace-write"
        assert loaded.llm.thinking_enabled is True
        assert loaded.iteration_budget == 20

    def test_save_load_round_trip_with_event_contracts(self, tmp_path: Path) -> None:
        """produces/consumes/fan_in survive a save → load cycle."""
        from niuu.domain.outcome import OutcomeField

        config = PersonaConfig(
            name="contract-agent",
            system_prompt_template="Agent with contracts.",
            produces=PersonaProduces(
                event_type="review.completed",
                schema={
                    "verdict": OutcomeField(
                        type="enum",
                        description="Pass or fail",
                        enum_values=["pass", "fail"],
                        required=True,
                    ),
                    "summary": OutcomeField(type="string", description="Summary", required=True),
                },
            ),
            consumes=PersonaConsumes(
                event_types=["code.changed", "review.requested"],
                injects=["repo", "branch"],
                schema={
                    "topic": OutcomeField(
                        type="string",
                        description="Research topic",
                        required=True,
                    ),
                    "max_sources": OutcomeField(
                        type="number",
                        description="Maximum sources to consult",
                        required=False,
                    ),
                },
            ),
            fan_in=PersonaFanIn(strategy="all_must_pass", contributes_to="review.verdict"),
        )
        dest_dir = tmp_path / ".ravn" / "personas"
        dest_dir.mkdir(parents=True, exist_ok=True)

        import unittest.mock as mock

        with mock.patch("ravn.adapters.personas.loader.Path") as mock_path_cls:
            real_path = Path
            mock_path_cls.side_effect = real_path
            mock_path_cls.home.return_value = tmp_path
            mock_path_cls.cwd = real_path.cwd

            loader = FilesystemPersonaAdapter()
            loader.save(config)

        loader2 = FilesystemPersonaAdapter([str(dest_dir)], include_builtin=False)
        loaded = loader2.load("contract-agent")
        assert loaded is not None
        # produces round-trips
        assert loaded.produces.event_type == "review.completed"
        assert "verdict" in loaded.produces.schema
        assert loaded.produces.schema["verdict"].type == "enum"
        assert loaded.produces.schema["verdict"].enum_values == ["pass", "fail"]
        assert "summary" in loaded.produces.schema
        # consumes round-trips
        assert loaded.consumes.event_types == ["code.changed", "review.requested"]
        assert loaded.consumes.injects == ["repo", "branch"]
        assert "topic" in loaded.consumes.schema
        assert loaded.consumes.schema["topic"].type == "string"
        assert "max_sources" in loaded.consumes.schema
        assert loaded.consumes.schema["max_sources"].type == "number"
        # fan_in round-trips
        assert loaded.fan_in.strategy == "all_must_pass"
        assert loaded.fan_in.contributes_to == "review.verdict"

    def test_save_creates_directory_if_missing(self, tmp_path: Path) -> None:
        config = PersonaConfig(name="new-agent", system_prompt_template="Test.")
        dest_dir = tmp_path / ".ravn" / "personas"
        assert not dest_dir.exists()

        import unittest.mock as mock

        with mock.patch("ravn.adapters.personas.loader.Path") as mock_path_cls:
            real_path = Path
            mock_path_cls.side_effect = real_path
            mock_path_cls.home.return_value = tmp_path
            mock_path_cls.cwd = real_path.cwd

            loader = FilesystemPersonaAdapter()
            loader.save(config)

        assert dest_dir.is_dir()
        assert (dest_dir / "new-agent.yaml").is_file()

    def test_save_overwrites_existing(self, tmp_path: Path) -> None:
        dest_dir = tmp_path / ".ravn" / "personas"
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / "agent.yaml").write_text("name: agent\nsystem_prompt_template: old\n")

        config = PersonaConfig(name="agent", system_prompt_template="new content")

        import unittest.mock as mock

        with mock.patch("ravn.adapters.personas.loader.Path") as mock_path_cls:
            real_path = Path
            mock_path_cls.side_effect = real_path
            mock_path_cls.home.return_value = tmp_path
            mock_path_cls.cwd = real_path.cwd

            loader = FilesystemPersonaAdapter()
            loader.save(config)

        content = (dest_dir / "agent.yaml").read_text()
        assert "new content" in content


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


class TestDelete:
    def test_delete_existing_file_returns_true(self, tmp_path: Path) -> None:
        yaml = _SIMPLE_PERSONA_YAML.replace("custom-agent", "my-agent")
        _write_persona(tmp_path, "my-agent", yaml)
        loader = FilesystemPersonaAdapter([str(tmp_path)])
        result = loader.delete("my-agent")
        assert result is True
        assert not (tmp_path / "my-agent.yaml").exists()

    def test_delete_nonexistent_returns_false(self, tmp_path: Path) -> None:
        loader = FilesystemPersonaAdapter([str(tmp_path)])
        result = loader.delete("does-not-exist")
        assert result is False

    def test_delete_builtin_without_override_returns_false(self, tmp_path: Path) -> None:
        """Pure built-in with no file returns False."""
        loader = FilesystemPersonaAdapter([str(tmp_path)])
        # coding-agent is built-in but no file in tmp_path
        result = loader.delete("coding-agent")
        assert result is False

    def test_delete_then_load_falls_back_to_builtin(self, tmp_path: Path) -> None:
        """After deleting override file, load() falls back to built-in."""
        builtin_yaml = _SIMPLE_PERSONA_YAML.replace("custom-agent", "coding-agent").replace(
            "You are a custom agent.", "Override!"
        )
        _write_persona(tmp_path, "coding-agent", builtin_yaml)

        loader = FilesystemPersonaAdapter([str(tmp_path)])
        persona_before = loader.load("coding-agent")
        assert persona_before is not None
        assert "Override!" in persona_before.system_prompt_template

        loader.delete("coding-agent")
        persona_after = loader.load("coding-agent")
        assert persona_after is not None
        # Now should come from built-in
        assert "Override!" not in persona_after.system_prompt_template

    def test_delete_removes_first_matching_file(self, tmp_path: Path) -> None:
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_persona(dir_a, "shared", _SIMPLE_PERSONA_YAML.replace("custom-agent", "shared"))
        _write_persona(dir_b, "shared", _OTHER_PERSONA_YAML.replace("other-agent", "shared"))

        loader = FilesystemPersonaAdapter([str(dir_a), str(dir_b)])
        result = loader.delete("shared")
        assert result is True
        # dir_a file should be gone
        assert not (dir_a / "shared.yaml").exists()
        # dir_b file still exists
        assert (dir_b / "shared.yaml").exists()


# ---------------------------------------------------------------------------
# is_builtin
# ---------------------------------------------------------------------------


class TestIsBuiltin:
    def test_builtin_returns_true(self) -> None:
        loader = FilesystemPersonaAdapter()
        for name in _BUILTIN_NAMES:
            assert loader.is_builtin(name) is True

    def test_custom_name_returns_false(self) -> None:
        loader = FilesystemPersonaAdapter()
        assert loader.is_builtin("my-custom-agent") is False

    def test_is_builtin_checks_bundled_dir_only(self, tmp_path: Path) -> None:
        """is_builtin checks the bundled dir, not user persona dirs."""
        yaml = _SIMPLE_PERSONA_YAML.replace("custom-agent", "file-only")
        _write_persona(tmp_path, "file-only", yaml)
        loader = FilesystemPersonaAdapter([str(tmp_path)])
        assert loader.is_builtin("file-only") is False


# ---------------------------------------------------------------------------
# load_all
# ---------------------------------------------------------------------------


class TestLoadAll:
    def test_load_all_returns_list(self) -> None:
        loader = FilesystemPersonaAdapter()
        all_personas = loader.load_all()
        assert isinstance(all_personas, list)
        assert len(all_personas) > 0

    def test_load_all_contains_all_builtins(self) -> None:
        loader = FilesystemPersonaAdapter()
        names = {p.name for p in loader.load_all()}
        for builtin in _BUILTIN_NAMES:
            assert builtin in names

    def test_load_all_includes_file_personas(self, tmp_path: Path) -> None:
        _write_persona(tmp_path, "custom-agent", _SIMPLE_PERSONA_YAML)
        loader = FilesystemPersonaAdapter([str(tmp_path)])
        names = {p.name for p in loader.load_all()}
        assert "custom-agent" in names

    def test_load_all_no_none_entries(self) -> None:
        loader = FilesystemPersonaAdapter()
        all_personas = loader.load_all()
        assert all(p is not None for p in all_personas)

    def test_load_all_are_persona_config_instances(self) -> None:
        loader = FilesystemPersonaAdapter()
        for persona in loader.load_all():
            assert isinstance(persona, PersonaConfig)


# ---------------------------------------------------------------------------
# source
# ---------------------------------------------------------------------------


class TestSource:
    def test_source_returns_builtin_for_builtin_name(self) -> None:
        loader = FilesystemPersonaAdapter()
        src = loader.source("coding-agent")
        assert src == "[built-in]"

    def test_source_returns_file_path_for_file_persona(self, tmp_path: Path) -> None:
        expected = _write_persona(tmp_path, "custom-agent", _SIMPLE_PERSONA_YAML)
        loader = FilesystemPersonaAdapter([str(tmp_path)])
        src = loader.source("custom-agent")
        assert src == str(expected)

    def test_source_returns_first_match(self, tmp_path: Path) -> None:
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        yaml_a = _SIMPLE_PERSONA_YAML.replace("custom-agent", "shared")
        yaml_b = _OTHER_PERSONA_YAML.replace("other-agent", "shared")
        file_a = _write_persona(dir_a, "shared", yaml_a)
        _write_persona(dir_b, "shared", yaml_b)

        loader = FilesystemPersonaAdapter([str(dir_a), str(dir_b)])
        src = loader.source("shared")
        assert src == str(file_a)

    def test_source_returns_empty_string_for_unknown(self, tmp_path: Path) -> None:
        loader = FilesystemPersonaAdapter([str(tmp_path)])
        src = loader.source("completely-unknown-persona")
        assert src == ""

    def test_source_file_overrides_builtin(self, tmp_path: Path) -> None:
        """A user file for a built-in name returns the file path, not '[built-in]'."""
        file = _write_persona(
            tmp_path,
            "coding-agent",
            _SIMPLE_PERSONA_YAML.replace("custom-agent", "coding-agent"),
        )
        loader = FilesystemPersonaAdapter([str(tmp_path)])
        src = loader.source("coding-agent")
        assert src == str(file)

    def test_source_builtin_without_files(self, tmp_path: Path) -> None:
        loader = FilesystemPersonaAdapter([], include_builtin=True)
        src = loader.source("reviewer")
        assert src == "[built-in]"

    def test_source_returns_empty_when_builtin_excluded(self, tmp_path: Path) -> None:
        loader = FilesystemPersonaAdapter([], include_builtin=False)
        src = loader.source("coding-agent")
        assert src == ""


# ---------------------------------------------------------------------------
# Backward compatibility — default constructor behaves as before + project-local
# ---------------------------------------------------------------------------


class TestBackwardCompat:
    def test_default_constructor_loads_builtin(self) -> None:
        """Default (no args) still resolves built-in personas."""
        loader = FilesystemPersonaAdapter()
        persona = loader.load("coding-agent")
        assert persona is not None
        assert persona.name == "coding-agent"

    def test_default_constructor_list_names_has_builtins(self) -> None:
        loader = FilesystemPersonaAdapter()
        names = loader.list_names()
        assert "coder" in names
        assert "reviewer" in names

    def test_default_constructor_adds_project_local_to_resolution(self, tmp_path: Path) -> None:
        """Default cwd creates .ravn/personas/ as project-local layer."""
        project_personas = tmp_path / ".ravn" / "personas"
        _write_persona(
            project_personas,
            "local-persona",
            _SIMPLE_PERSONA_YAML.replace("custom-agent", "local-persona"),
        )
        loader = FilesystemPersonaAdapter(cwd=tmp_path)
        assert "local-persona" in loader.list_names()
        persona = loader.load("local-persona")
        assert persona is not None
