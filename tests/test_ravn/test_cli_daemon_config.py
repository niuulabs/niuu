"""Tests for the shared daemon config generator used by flock and room."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ravn.cli.daemon_config import (
    build_member_config,
    load_base_config,
    render_cluster_yaml,
    render_member_config,
    render_skuld_yaml,
    room_broker_ws_url,
    skuld_block,
)


class TestBrokerUrl:
    def test_points_at_the_ravn_participant_endpoint(self) -> None:
        assert room_broker_ws_url("127.0.0.1", 7500) == "ws://127.0.0.1:7500/ws/ravn"


class TestSkuldBlock:
    def test_enables_the_room_channel(self) -> None:
        block = skuld_block("ws://x/ws/ravn", display_name="reviewer")

        assert block == {
            "enabled": True,
            "broker_url": "ws://x/ws/ravn",
            "display_name": "reviewer",
        }

    def test_display_name_is_optional(self) -> None:
        assert "display_name" not in skuld_block("ws://x/ws/ravn")

    def test_renders_as_a_top_level_section(self) -> None:
        parsed = yaml.safe_load(render_skuld_yaml("ws://x/ws/ravn"))

        assert parsed["skuld"]["enabled"] is True


class TestMemberConfig:
    def test_handle_becomes_the_mesh_peer_id(self) -> None:
        """The peer id is the member's room identity — it must be the handle."""
        config = build_member_config(handle="reviewer", broker_url="ws://x/ws/ravn")

        assert config["mesh"]["own_peer_id"] == "reviewer"

    def test_initiative_is_enabled(self) -> None:
        """The drive loop owns the room connection; without it nothing registers."""
        config = build_member_config(handle="reviewer", broker_url="ws://x/ws/ravn")

        assert config["initiative"]["enabled"] is True

    def test_self_driving_triggers_are_off_by_default(self) -> None:
        config = build_member_config(handle="reviewer", broker_url="ws://x/ws/ravn")

        assert config["mimir"]["source_trigger"]["enabled"] is False
        assert config["mimir"]["staleness_trigger"]["enabled"] is False
        assert config["resident_wakefulness"]["enabled"] is False
        assert config["dream_cycle"]["enabled"] is False

    def test_autonomous_leaves_triggers_alone(self) -> None:
        config = build_member_config(
            handle="reviewer", broker_url="ws://x/ws/ravn", autonomous=True
        )

        assert "resident_wakefulness" not in config
        assert "dream_cycle" not in config

    def test_paths_are_threaded_through(self, tmp_path: Path) -> None:
        config = build_member_config(
            handle="reviewer",
            broker_url="ws://x/ws/ravn",
            memory_db_path=tmp_path / "memory.db",
            queue_journal_path=tmp_path / "queue.json",
        )

        assert config["memory"]["sqlite"]["path"] == str(tmp_path / "memory.db")
        assert config["initiative"]["queue_journal_path"] == str(tmp_path / "queue.json")
        # The queue path must not clobber the enabled flag it shares a section with.
        assert config["initiative"]["enabled"] is True


class TestBaseLayering:
    def test_base_settings_survive(self) -> None:
        base = {"llm": {"model": "some-model", "max_tokens": 4096}}

        config = build_member_config(handle="r", broker_url="ws://x/ws/ravn", base=base)

        assert config["llm"] == {"model": "some-model", "max_tokens": 4096}

    def test_nested_base_sections_merge_rather_than_replace(self) -> None:
        base = {"memory": {"backend": "sqlite", "sqlite": {"pragma": "WAL"}}}

        config = build_member_config(
            handle="r",
            broker_url="ws://x/ws/ravn",
            memory_db_path=Path("/tmp/m.db"),
            base=base,
        )

        assert config["memory"]["sqlite"]["pragma"] == "WAL"
        assert config["memory"]["sqlite"]["path"] == "/tmp/m.db"

    def test_membership_wins_over_a_conflicting_base(self) -> None:
        """A base config must not be able to point a member at another room."""
        base = {"skuld": {"enabled": False, "broker_url": "ws://elsewhere/ws/ravn"}}

        config = build_member_config(handle="r", broker_url="ws://x/ws/ravn", base=base)

        assert config["skuld"]["enabled"] is True
        assert config["skuld"]["broker_url"] == "ws://x/ws/ravn"


class TestLoadBaseConfig:
    def test_none_yields_empty(self) -> None:
        assert load_base_config(None) == {}

    def test_empty_file_yields_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "base.yaml"
        path.write_text("", encoding="utf-8")

        assert load_base_config(path) == {}

    def test_mapping_is_returned(self, tmp_path: Path) -> None:
        path = tmp_path / "base.yaml"
        path.write_text("llm:\n  model: x\n", encoding="utf-8")

        assert load_base_config(path) == {"llm": {"model": "x"}}

    def test_non_mapping_raises_rather_than_degrading(self, tmp_path: Path) -> None:
        path = tmp_path / "base.yaml"
        path.write_text("- just\n- a list\n", encoding="utf-8")

        with pytest.raises(ValueError, match="not a YAML mapping"):
            load_base_config(path)

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(OSError):
            load_base_config(tmp_path / "absent.yaml")


class TestRenderMemberConfig:
    def test_output_is_parseable_yaml_with_provenance(self) -> None:
        rendered = render_member_config(
            handle="reviewer",
            room_name="desk",
            persona="reviewer",
            broker_url="ws://x/ws/ravn",
        )

        assert "# Generated by: ravn join" in rendered
        assert "member 'reviewer' of room 'desk'" in rendered
        parsed = yaml.safe_load(rendered)
        assert parsed["skuld"]["broker_url"] == "ws://x/ws/ravn"
        assert parsed["mesh"]["own_peer_id"] == "reviewer"


class TestMemberMeshWiring:
    """A room member must be a real mesh peer, not just a seat in a room."""

    def _config(self, tmp_path: Path) -> dict:
        return build_member_config(
            handle="reviewer",
            broker_url="ws://x/ws/ravn",
            mesh_ports=(7600, 7601),
            cluster_file=tmp_path / "cluster.yaml",
        )

    def test_mesh_is_enabled_with_allocated_sockets(self, tmp_path: Path) -> None:
        config = self._config(tmp_path)

        assert config["mesh"]["enabled"] is True
        assert config["mesh"]["adapter"] == "nng"
        assert config["mesh"]["own_peer_id"] == "reviewer"
        assert config["mesh"]["nng"]["pub_sub_address"] == "tcp://0.0.0.0:7600"
        assert config["mesh"]["nng"]["req_rep_address"] == "tcp://0.0.0.0:7601"

    def test_discovery_uses_the_adapters_list(self, tmp_path: Path) -> None:
        """The builder only reads `adapters`; the legacy `adapter` key builds nothing."""
        config = self._config(tmp_path)

        adapters = config["discovery"]["adapters"]
        assert config["discovery"]["enabled"] is True
        assert adapters[0]["adapter"].endswith("StaticDiscoveryAdapter")
        assert adapters[0]["cluster_file"] == str(tmp_path / "cluster.yaml")
        assert "adapter" not in config["discovery"]

    def test_cascade_is_enabled_for_delegation(self, tmp_path: Path) -> None:
        assert self._config(tmp_path)["cascade"]["enabled"] is True

    def test_without_ports_no_mesh_is_configured(self) -> None:
        config = build_member_config(handle="reviewer", broker_url="ws://x/ws/ravn")

        assert config["mesh"] == {"own_peer_id": "reviewer"}
        assert "discovery" not in config

    def test_logging_defaults_to_info_but_base_wins(self) -> None:
        """A detached member needs a readable log, without overriding the operator."""
        default = build_member_config(handle="r", broker_url="ws://x/ws/ravn")
        overridden = build_member_config(
            handle="r", broker_url="ws://x/ws/ravn", base={"logging": {"level": "DEBUG"}}
        )

        assert default["logging"]["level"] == "INFO"
        assert overridden["logging"]["level"] == "DEBUG"


class TestClusterYaml:
    def test_renders_every_member_as_a_peer(self) -> None:
        rendered = render_cluster_yaml(
            [
                {"handle": "reviewer", "persona": "reviewer", "pub_port": 7600, "rep_port": 7601},
                {"handle": "builder", "persona": "coder", "pub_port": 7602, "rep_port": 7603},
            ]
        )

        assert "peer_id: reviewer" in rendered
        assert "peer_id: builder" in rendered
        assert 'pub_address: "tcp://127.0.0.1:7600"' in rendered
        assert 'rep_address: "tcp://127.0.0.1:7603"' in rendered

    def test_an_empty_room_renders_an_empty_table(self) -> None:
        rendered = render_cluster_yaml([])

        assert "peers:" in rendered
        assert "peer_id" not in rendered
