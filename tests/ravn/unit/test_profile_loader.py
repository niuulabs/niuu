"""Unit tests for ProfileLoader."""

from __future__ import annotations

from pathlib import Path

from ravn.adapters.profiles.loader import ProfileLoader
from ravn.domain.profile import RavnProfile

# ---------------------------------------------------------------------------
# Built-in profiles
# ---------------------------------------------------------------------------


class TestBuiltinProfiles:
    def test_local_profile_exists(self) -> None:
        loader = ProfileLoader()
        p = loader.load("local")
        assert p is not None
        assert p.name == "local"

    def test_tanngrisnir_profile_exists(self) -> None:
        loader = ProfileLoader()
        p = loader.load("tanngrisnir")
        assert p is not None
        assert p.name == "tanngrisnir"
        assert p.location == "gimle"
        assert p.deployment == "k8s"
        assert "coding" in p.specialisations

    def test_huginn_profile_exists(self) -> None:
        loader = ProfileLoader()
        p = loader.load("huginn")
        assert p is not None
        assert p.deployment == "mobile"

    def test_list_builtin_names_returns_sorted(self) -> None:
        names = ProfileLoader().list_builtin_names()
        assert names == sorted(names)
        assert "local" in names
        assert "tanngrisnir" in names

    def test_unknown_name_returns_none(self) -> None:
        assert ProfileLoader().load("does_not_exist") is None


# ---------------------------------------------------------------------------
# parse() — YAML text parsing
# ---------------------------------------------------------------------------


class TestProfileLoaderParse:
    def test_minimal_valid_yaml(self) -> None:
        yaml_text = "name: mybot\n"
        p = ProfileLoader.parse(yaml_text)
        assert p is not None
        assert p.name == "mybot"
        assert p.rune == "ᚱ"

    def test_empty_string_returns_none(self) -> None:
        assert ProfileLoader.parse("") is None
        assert ProfileLoader.parse("   \n  ") is None

    def test_no_name_returns_none(self) -> None:
        assert ProfileLoader.parse("location: gimle\n") is None

    def test_invalid_yaml_returns_none(self) -> None:
        assert ProfileLoader.parse(": : invalid: [yaml") is None

    def test_full_profile_parsed(self) -> None:
        yaml_text = """\
name: tanngrisnir
rune: "ᚱ"
location: gimle
deployment: k8s
persona: autonomous-agent
system_prompt_extra: "You are on Valaskjalf."
specialisations:
  - infrastructure
  - coding
fallback_model: ""
mcp_servers:
  - linear
  - gmail
gateway_channels:
  - skuld
sleipnir_topics: []
output_mode: ambient
mimir_mounts:
  - name: gimle-wiki
    role: primary
    priority: 10
mimir_write_routing:
  "wiki/": gimle-wiki
cascade_mode: networked
trigger_names:
  - morning-review
checkpoint_enabled: true
checkpoint_strategy: on_milestone
"""
        p = ProfileLoader.parse(yaml_text)
        assert p is not None
        assert p.name == "tanngrisnir"
        assert p.location == "gimle"
        assert p.deployment == "k8s"
        assert p.persona == "autonomous-agent"
        assert p.system_prompt_extra == "You are on Valaskjalf."
        assert p.specialisations == ["infrastructure", "coding"]
        assert p.mcp_servers == ["linear", "gmail"]
        assert p.gateway_channels == ["skuld"]
        assert p.output_mode == "ambient"
        assert len(p.mimir_mounts) == 1
        assert p.mimir_mounts[0].name == "gimle-wiki"
        assert p.mimir_mounts[0].role == "primary"
        assert p.mimir_mounts[0].priority == 10
        assert p.mimir_write_routing == {"wiki/": "gimle-wiki"}
        assert p.cascade_mode == "networked"
        assert p.trigger_names == ["morning-review"]
        assert p.checkpoint_enabled is True
        assert p.checkpoint_strategy == "on_milestone"

    def test_checkpoint_enabled_bool_variants(self) -> None:
        for val in ("true", "yes", "1"):
            p = ProfileLoader.parse(f"name: x\ncheckpoint_enabled: {val}\n")
            assert p is not None and p.checkpoint_enabled is True

        for val in ("false", "no", "0"):
            p = ProfileLoader.parse(f"name: x\ncheckpoint_enabled: {val}\n")
            assert p is not None and p.checkpoint_enabled is False

    def test_mimir_mounts_with_missing_name_skipped(self) -> None:
        yaml_text = """\
name: x
mimir_mounts:
  - role: primary
  - name: valid-mount
    role: archive
"""
        p = ProfileLoader.parse(yaml_text)
        assert p is not None
        assert len(p.mimir_mounts) == 1
        assert p.mimir_mounts[0].name == "valid-mount"

    def test_mimir_write_routing_non_dict_ignored(self) -> None:
        yaml_text = "name: x\nmimir_write_routing: not-a-dict\n"
        p = ProfileLoader.parse(yaml_text)
        assert p is not None
        assert p.mimir_write_routing == {}


# ---------------------------------------------------------------------------
# load_from_file()
# ---------------------------------------------------------------------------


class TestProfileLoaderFromFile:
    def test_loads_yaml_file(self, tmp_path: Path) -> None:
        f = tmp_path / "custom.yaml"
        f.write_text("name: custom\nlocation: test-lab\n")
        p = ProfileLoader(profiles_dir=tmp_path).load_from_file(f)
        assert p is not None
        assert p.name == "custom"
        assert p.location == "test-lab"

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        p = ProfileLoader(profiles_dir=tmp_path).load_from_file(tmp_path / "no.yaml")
        assert p is None

    def test_user_file_overrides_builtin(self, tmp_path: Path) -> None:
        f = tmp_path / "tanngrisnir.yaml"
        f.write_text("name: tanngrisnir\nlocation: custom-location\n")
        loader = ProfileLoader(profiles_dir=tmp_path)
        p = loader.load("tanngrisnir")
        assert p is not None
        assert p.location == "custom-location"

    def test_fallback_to_builtin_when_no_file(self, tmp_path: Path) -> None:
        loader = ProfileLoader(profiles_dir=tmp_path)
        p = loader.load("tanngrisnir")
        assert p is not None
        assert p.location == "gimle"


# ---------------------------------------------------------------------------
# to_yaml() — round-trip
# ---------------------------------------------------------------------------


class TestProfileLoaderToYaml:
    def test_yaml_round_trip(self) -> None:
        p = RavnProfile(
            name="round-trip",
            location="test",
            deployment="systemd",
            persona="coding-agent",
            specialisations=["coding"],
            mcp_servers=["linear"],
            checkpoint_enabled=True,
        )
        yaml_text = ProfileLoader.to_yaml(p)
        p2 = ProfileLoader.parse(yaml_text)
        assert p2 is not None
        assert p2.name == p.name
        assert p2.location == p.location
        assert p2.deployment == p.deployment
        assert p2.persona == p.persona
        assert p2.specialisations == p.specialisations
        assert p2.mcp_servers == p.mcp_servers
        assert p2.checkpoint_enabled == p.checkpoint_enabled

    def test_to_yaml_produces_string(self) -> None:
        p = RavnProfile(name="x")
        result = ProfileLoader.to_yaml(p)
        assert isinstance(result, str)
        assert "name: x" in result


# ---------------------------------------------------------------------------
# _safe_bool / _safe_int helpers (covering str / error paths)
# ---------------------------------------------------------------------------


class TestProfileLoaderHelpers:
    def test_safe_bool_string_true(self) -> None:
        from ravn.adapters.profiles.loader import _safe_bool

        assert _safe_bool("true") is True
        assert _safe_bool("yes") is True
        assert _safe_bool("1") is True

    def test_safe_bool_string_false(self) -> None:
        from ravn.adapters.profiles.loader import _safe_bool

        assert _safe_bool("false") is False
        assert _safe_bool("no") is False

    def test_safe_bool_unknown_type_returns_default(self) -> None:
        from ravn.adapters.profiles.loader import _safe_bool

        assert _safe_bool(None, default=True) is True
        assert _safe_bool([], default=False) is False

    def test_safe_int_invalid_returns_default(self) -> None:
        from ravn.adapters.profiles.loader import _safe_int

        assert _safe_int("not-a-number") == 0
        assert _safe_int(None, default=5) == 5

    def test_parse_non_dict_yaml_returns_none(self) -> None:
        """YAML that is a list, not a dict, must return None."""
        assert ProfileLoader.parse("- item1\n- item2\n") is None

    def test_list_names_discovers_yaml_files(self, tmp_path: Path) -> None:
        """list_names() unions built-ins with YAML files in profiles_dir."""
        (tmp_path / "my-profile.yaml").write_text("name: my-profile\n")
        loader = ProfileLoader(profiles_dir=tmp_path)
        names = loader.list_names()
        assert "my-profile" in names
        # Built-ins are still present
        assert "local" in names
