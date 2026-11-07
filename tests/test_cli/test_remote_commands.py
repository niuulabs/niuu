"""Tests for the `niuu remote` commands (fake Niuu host, fake ssh)."""

from __future__ import annotations

import json
import shlex
from typing import Any
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from cli.api.compute_hosts import ForgeAPIError
from cli.app import build_app
from cli.auth.credentials import StoredTokens
from cli.commands import remote
from cli.config import CLISettings
from cli.registry import PluginRegistry
from cli.services.remote_hosts import RunResult

runner = CliRunner()

COMMAND = "curl -fsSL -H 'Authorization: Bearer the-code' https://forge/join.sh | sudo sh"


def _host(**overrides: Any) -> dict[str, Any]:
    host: dict[str, Any] = {
        "id": "h1",
        "name": "gpu-box",
        "state": "pending",
        "health": "pending",
        "execution": "host-podman",
        "tags": ["gpu"],
        "slots": None,
        "sessions": [],
        "findings": [],
        "connection": {"kind": "ssh", "target": "10.0.0.5", "connected": False},
        "suggested_slots": None,
        "last_checked_at": None,
    }
    host.update(overrides)
    return host


READY = _host(
    state="ready",
    health="healthy",
    slots=4,
    connection={"kind": "ssh", "target": "10.0.0.5", "connected": True},
)


class FakeNiuuHost:
    """The Niuu host's facade for one Forge: hosts appear once the ssh run 'joins'."""

    def __init__(self, *, after_join: list[list[dict[str, Any]]] | None = None) -> None:
        self.hosts: list[dict[str, Any]] = [_host(id="old", name="old-box", state="ready")]
        self.after_join = after_join if after_join is not None else [[READY]]
        self.joined = False
        self.reads_after_join = 0
        self.minted: list[dict[str, Any]] = []
        self.revoked: list[str] = []
        self.drained: list[tuple[str, bool]] = []
        self.slots_set: list[tuple[str, int]] = []
        self.forge_list = [{"id": "f1", "slug": "ymir", "name": "Ymir"}]
        self.mint_error: ForgeAPIError | None = None
        self.revoke_error: ForgeAPIError | None = None

    def join(self) -> None:
        self.joined = True

    async def forges(self) -> list[dict[str, Any]]:
        return self.forge_list

    async def overview(self, instance_id: str) -> dict[str, Any]:
        assert instance_id == "f1"
        hosts = list(self.hosts)
        if self.joined and self.after_join:
            index = min(self.reads_after_join, len(self.after_join) - 1)
            self.reads_after_join += 1
            hosts += self.after_join[index]
        return {
            "pool_id": "hosts",
            "hosts": hosts,
            "summary": {"hosts": len(hosts), "slots_free": 3, "slots_total": 8, "last_error": None},
            "runtimes": [{"execution": "host-podman", "allowed": True, "refusal": None}],
            "connectors": [{"binding_id": "direct", "kind": "ssh"}],
            "pairing_codes": [],
        }

    async def mint(self, instance_id: str, body: dict[str, Any]) -> dict[str, Any]:
        if self.mint_error:
            raise self.mint_error
        self.minted.append(body)
        return {"id": "c1", "expires_at": "10:42", "code": "the-code", "command": COMMAND}

    async def revoke(self, instance_id: str, code_id: str) -> None:
        if self.revoke_error:
            raise self.revoke_error
        self.revoked.append(code_id)

    async def set_slots(self, instance_id: str, host_id: str, slots: int) -> dict[str, Any]:
        self.slots_set.append((host_id, slots))
        return {**READY, "slots": slots}

    async def drain(self, instance_id: str, host_id: str, *, remove: bool) -> dict[str, Any]:
        self.drained.append((host_id, remove))
        self.hosts = [h for h in self.hosts if h["id"] != host_id]
        return {}


def _settings() -> CLISettings:
    settings = CLISettings()
    settings.remote.poll_interval_seconds = 0.001
    return settings


def _invoke(fake: FakeNiuuHost, args: list[str], *, ssh: Any = None, settings=None):
    app = build_app(settings=settings or _settings(), registry=PluginRegistry())
    runs: list[list[str]] = []

    def run_streaming(argv, **_: Any) -> RunResult:
        runs.append(list(argv))
        if ssh is not None:
            return ssh(fake)
        fake.join()
        return RunResult(returncode=0, last_line="niuu: to undo: sudo niuu-host-leave")

    with (
        patch.object(remote, "build_api", return_value=fake),
        patch("cli.services.remote_hosts.run_streaming", run_streaming),
    ):
        result = runner.invoke(app, ["remote", *args])
    return result, runs


class TestJoin:
    def test_joins_and_follows_to_ready(self) -> None:
        fake = FakeNiuuHost(
            after_join=[[_host()], [{**READY}]],
        )
        result, runs = _invoke(fake, ["join", "admin@gpu-box", "--tag", "gpu", "-p", "2222"])
        assert result.exit_code == 0, result.output
        assert fake.minted == [{"execution": "host-podman", "connector": "direct", "tags": ["gpu"]}]
        [argv] = runs
        assert argv[:-1] == ["ssh", "-p", "2222", "--", "admin@gpu-box"]
        assert shlex.split(argv[-1]) == ["sh", "-c", COMMAND]
        assert "✓ minted pairing code for host-podman on Ymir (expires 10:42)" in result.output
        assert "→ admin@gpu-box: running join.sh" in result.output
        assert "✓ ready        tags: gpu" in result.output
        assert "Next: `niuu remote hosts gpu-box`" in result.output

    def test_passes_mint_flags(self) -> None:
        fake = FakeNiuuHost()
        result, _ = _invoke(
            fake,
            [
                "join",
                "box",
                "--execution",
                "host-gpu",
                "--connector",
                "reverse",
                "--slots",
                "2",
                "--isolation",
                "cgroupfs",
            ],
        )
        assert result.exit_code == 0, result.output
        assert fake.minted == [
            {
                "execution": "host-gpu",
                "connector": "reverse",
                "tags": [],
                "slots": 2,
                "isolation": "cgroupfs",
            }
        ]

    def test_suggested_slots_put_the_host_in_service(self) -> None:
        waiting = _host(
            state="ready",
            health="healthy",
            connection={"connected": True},
            suggested_slots={"slots": 6, "lines": []},
        )
        fake = FakeNiuuHost(after_join=[[waiting], [waiting], [{**READY, "slots": 6}]])
        result, _ = _invoke(fake, ["join", "box", "--slots", "suggested"])
        assert result.exit_code == 0, result.output
        assert fake.slots_set == [("h1", 6)]
        assert "slots" not in fake.minted[0]

    def test_waits_for_capacity_and_prints_the_suggestion(self) -> None:
        waiting = _host(
            state="ready",
            health="healthy",
            connection={"connected": True},
            suggested_slots={"slots": 6, "lines": ["32 CPUs - 4 reserved = 28; 28 / 4 = 7"]},
        )
        fake = FakeNiuuHost(after_join=[[waiting]])
        result, _ = _invoke(fake, ["join", "box"])
        assert result.exit_code == 0, result.output
        assert "waits for its slot count" in result.output
        assert "  32 CPUs - 4 reserved" in result.output
        assert "Set it: niuu remote slots gpu-box 6" in result.output

    def test_waits_for_capacity_without_a_suggestion(self) -> None:
        waiting = _host(state="ready", health="healthy", connection={"connected": True})
        result, _ = _invoke(FakeNiuuHost(after_join=[[waiting]]), ["join", "box"])
        assert result.exit_code == 0, result.output
        assert "Set it: niuu remote slots gpu-box <n>" in result.output

    def test_no_follow_stops_once_registered(self) -> None:
        fake = FakeNiuuHost(after_join=[[_host()]])
        result, _ = _invoke(fake, ["join", "box", "--no-follow"])
        assert result.exit_code == 0, result.output
        assert "✓ joined       gpu-box (h1)" in result.output
        assert "connected" not in result.output

    def test_quarantined_host_exits_1_with_findings(self) -> None:
        bad = _host(
            state="quarantined",
            health="quarantined",
            connection={"connected": True},
            findings=["docker group has alice"],
        )
        result, _ = _invoke(FakeNiuuHost(after_join=[[bad]]), ["join", "box"])
        assert result.exit_code == 1
        assert "error: gpu-box is quarantined:" in result.output
        assert "docker group has alice" in result.output

    def test_failed_script_revokes_the_unused_code(self) -> None:
        fake = FakeNiuuHost()

        def fails(_: FakeNiuuHost) -> RunResult:
            return RunResult(returncode=1, last_line="niuu: cgroup v2 is required")

        result, _ = _invoke(fake, ["join", "box"], ssh=fails)
        assert result.exit_code == 1
        assert fake.revoked == ["c1"]
        assert "join.sh failed on box (exit 1): niuu: cgroup v2 is required" in result.output

    def test_failed_ssh_is_named_and_a_failed_revoke_is_reported(self) -> None:
        fake = FakeNiuuHost()
        fake.revoke_error = ForgeAPIError("revoking the pairing code: 409 already used")

        def fails(_: FakeNiuuHost) -> RunResult:
            return RunResult(returncode=255, last_line="")

        result, _ = _invoke(fake, ["join", "box"], ssh=fails)
        assert result.exit_code == 1
        assert "warning: the unused pairing code c1 was not revoked" in result.output
        assert "ssh failed on box (exit 255): no output" in result.output

    def test_script_failing_after_registering_keeps_the_code(self) -> None:
        fake = FakeNiuuHost(after_join=[[_host()]])

        def registers_then_fails(niuu: FakeNiuuHost) -> RunResult:
            niuu.join()
            return RunResult(returncode=1, last_line="niuu: the connector setup failed")

        result, _ = _invoke(fake, ["join", "box"], ssh=registers_then_fails)
        assert result.exit_code == 1
        assert fake.revoked == []

    def test_exit_0_without_a_new_host_is_a_failed_join(self) -> None:
        fake = FakeNiuuHost()

        def downloads_nothing(_: FakeNiuuHost) -> RunResult:
            return RunResult(returncode=0, last_line="curl: (22) 401")

        result, _ = _invoke(fake, ["join", "box"], ssh=downloads_nothing)
        assert result.exit_code == 1
        assert "did not register a host" in result.output

    def test_forge_refusal_is_printed(self) -> None:
        fake = FakeNiuuHost()
        fake.mint_error = ForgeAPIError("minting a pairing code: 422 extra inputs: isolation")
        result, runs = _invoke(fake, ["join", "box", "--isolation", "none"])
        assert result.exit_code == 1
        assert "422 extra inputs: isolation" in result.output
        assert runs == []

    def test_timeout_flag_bounds_following(self) -> None:
        result, _ = _invoke(FakeNiuuHost(after_join=[[_host()]]), ["join", "box", "--timeout", "0"])
        assert result.exit_code == 1
        assert "after 0 s" in result.output

    @pytest.mark.parametrize(
        ("args", "message"),
        [
            (["join", "box", "--isolation", "yolo"], "--isolation"),
            (["join", "box", "--slots", "0"], "--slots"),
            (["join", "box", "--slots", "many"], "--slots"),
            (["join", "--", "-oProxyCommand=x"], "DESTINATION"),
        ],
    )
    def test_usage_errors_exit_2(self, args: list[str], message: str) -> None:
        fake = FakeNiuuHost()
        result, runs = _invoke(fake, args)
        assert result.exit_code == 2
        assert message in result.output
        assert fake.minted == [] and runs == []

    def test_ctrl_c_stops_following(self) -> None:
        fake = FakeNiuuHost()

        def interrupted(_: FakeNiuuHost) -> RunResult:
            raise KeyboardInterrupt

        result, _ = _invoke(fake, ["join", "box"], ssh=interrupted)
        assert result.exit_code == remote.EXIT_INTERRUPTED
        assert "Stopped following" in result.output


class TestHosts:
    def test_table_with_findings(self) -> None:
        fake = FakeNiuuHost()
        fake.hosts.append(
            _host(
                id="h2",
                name="sick-box",
                state="quarantined",
                isolation="none",
                slots=1,
                sessions=[{"slot": 0}],
                findings=["leftover slot user niuu-slot-0"],
            )
        )
        result, _ = _invoke(fake, ["hosts"])
        assert result.exit_code == 0, result.output
        lines = result.output.splitlines()
        assert lines[0] == "Ymir · pool hosts · 2 hosts · 3/8 slots free"
        assert lines[1].split() == [
            "NAME",
            "STATE",
            "ISOLATION",
            "SLOTS",
            "TAGS",
            "RUNTIME",
            "CONNECTION",
            "CHECKED",
        ]
        assert "sick-box" in lines[3] and "none" in lines[3] and "1/1" in lines[3]
        assert "ssh 10.0.0.5 (down)" in lines[3]
        assert lines[4] == "  ! leftover slot user niuu-slot-0"

    def test_empty_pool_and_pool_error(self) -> None:
        fake = FakeNiuuHost()
        fake.hosts = []

        async def overview(_: str) -> dict[str, Any]:
            return {
                "pool_id": "hosts",
                "hosts": [],
                "summary": {"last_error": "boom"},
                "pairing_codes": [{"id": "c"}],
            }

        fake.overview = overview  # type: ignore[method-assign]
        result, _ = _invoke(fake, ["hosts"])
        assert result.exit_code == 0, result.output
        assert "error: boom" in result.output
        assert "No hosts. Join one" in result.output

    def test_unused_codes_and_no_connection(self) -> None:
        fake = FakeNiuuHost()
        fake.hosts = [_host(connection=None)]
        original = fake.overview

        async def overview(instance_id: str) -> dict[str, Any]:
            return {**await original(instance_id), "pairing_codes": [{"id": "a"}, {"id": "b"}]}

        fake.overview = overview  # type: ignore[method-assign]
        result, _ = _invoke(fake, ["hosts"])
        assert "2 unused pairing codes" in result.output
        assert "0/?" in result.output

    def test_one_host_shows_its_steps(self) -> None:
        fake = FakeNiuuHost()
        fake.hosts = [
            _host(
                state="ready",
                health="healthy",
                connection={"connected": True},
                findings=["clock skew"],
                suggested_slots={"slots": 2, "lines": ["8 GiB / 4 GiB = 2"]},
            )
        ]
        result, _ = _invoke(fake, ["hosts", "gpu-box"])
        assert result.exit_code == 0, result.output
        assert result.output.splitlines()[0] == "gpu-box (h1) · ready"
        assert "… capacity" in result.output
        assert "! clock skew" in result.output
        assert "Forge suggests 2 slots:" in result.output

    def test_json(self) -> None:
        fake = FakeNiuuHost()
        result, _ = _invoke(fake, ["hosts", "--json"])
        assert json.loads(result.output)["pool_id"] == "hosts"
        result, _ = _invoke(fake, ["hosts", "old-box", "--json"])
        assert json.loads(result.output)["id"] == "old"

    def test_several_forges_need_a_choice(self) -> None:
        fake = FakeNiuuHost()
        fake.forge_list.append({"id": "f2", "slug": "valhalla", "name": "Valhalla"})
        result, _ = _invoke(fake, ["hosts"])
        assert result.exit_code == 1
        assert "pick one with --forge" in result.output
        result, _ = _invoke(fake, ["hosts", "--forge", "ymir"])
        assert result.exit_code == 0, result.output


class TestSlots:
    def test_sets_the_count(self) -> None:
        fake = FakeNiuuHost()
        result, _ = _invoke(fake, ["slots", "old-box", "3"])
        assert result.exit_code == 0, result.output
        assert fake.slots_set == [("old", 3)]
        assert "✓ gpu-box: 3 slots, ready" in result.output


class TestLeave:
    def test_drains_waits_and_runs_the_guarded_leave(self) -> None:
        fake = FakeNiuuHost()

        def leaves(_: FakeNiuuHost) -> RunResult:
            return RunResult(returncode=0, last_line="niuu: old-box left the Forge")

        result, runs = _invoke(fake, ["leave", "old-box", "-J", "bastion"], ssh=leaves)
        assert result.exit_code == 0, result.output
        assert fake.drained == [("old", True)]
        [argv] = runs
        assert argv[:-1] == ["ssh", "-J", "bastion", "--", "old-box"]
        inner = shlex.split(argv[-1])
        assert inner[:2] == ["sh", "-c"]
        assert "niuu-host-leave" in inner[2] and "old" in inner[2]
        assert "✓ Forge forgot old-box" in result.output
        assert "✓ old-box left" in result.output

    def test_ssh_flag_overrides_the_destination(self) -> None:
        fake = FakeNiuuHost()

        def leaves(_: FakeNiuuHost) -> RunResult:
            return RunResult(returncode=0, last_line="")

        _, runs = _invoke(fake, ["leave", "old", "--ssh", "ssh://root@10.0.0.9:2200"], ssh=leaves)
        assert runs[0][:-1] == ["ssh", "-p", "2200", "--", "root@10.0.0.9"]

    def test_failed_host_side_leave_says_what_to_run(self) -> None:
        fake = FakeNiuuHost()

        def refuses(_: FakeNiuuHost) -> RunResult:
            return RunResult(returncode=1, last_line="niuu: this machine joined as host x")

        result, _ = _invoke(fake, ["leave", "old-box"], ssh=refuses)
        assert result.exit_code == 1
        assert "joined as host x" in result.output
        assert "run `sudo niuu-host-leave` there" in result.output

    def test_timeout_while_draining(self) -> None:
        fake = FakeNiuuHost()

        async def drain(instance_id: str, host_id: str, *, remove: bool) -> dict[str, Any]:
            fake.hosts = [{**fake.hosts[0], "state": "removing", "sessions": [{"slot": 0}]}]
            return {}

        fake.drain = drain  # type: ignore[method-assign]
        result, runs = _invoke(fake, ["leave", "old-box", "--timeout", "0"])
        assert result.exit_code == 1
        assert "still removing, 1 session left" in result.output
        assert runs == []

    def test_ctrl_c_while_waiting(self) -> None:
        fake = FakeNiuuHost()

        async def drain(instance_id: str, host_id: str, *, remove: bool) -> dict[str, Any]:
            raise KeyboardInterrupt

        fake.drain = drain  # type: ignore[method-assign]
        result, _ = _invoke(fake, ["leave", "old-box"])
        assert result.exit_code == remote.EXIT_INTERRUPTED
        assert "Forge still removes the host" in result.output


class TestBuildApi:
    def test_uses_the_login_token_and_refreshes_with_its_client(self) -> None:
        tokens = StoredTokens(
            access_token="operator-token",
            refresh_token="r",
            issuer="https://idp.example",
            client_id="niuu-cli",
        )
        with patch("cli.auth.credentials.CredentialStore.load", return_value=tokens):
            api = remote.build_api(CLISettings(), "https://niuu.example/")
        client = api._client
        assert api.base_url == "https://niuu.example"
        assert client._headers() == {"Authorization": "Bearer operator-token"}
        assert client._refresh_token_fn is not None

    def test_without_a_login_sends_no_token_to_the_local_server(self) -> None:
        settings = CLISettings()
        settings.server.port = 9090
        with patch("cli.auth.credentials.CredentialStore.load", return_value=None):
            api = remote.build_api(settings, "")
        assert api.base_url == "http://127.0.0.1:9090"
        assert api._client._headers() == {}

    def test_remote_url_from_config_and_old_tokens_do_not_refresh(self) -> None:
        settings = CLISettings()
        settings.remote.url = "https://configured.example"
        tokens = StoredTokens(access_token="t", refresh_token="r", issuer="https://idp")
        with patch("cli.auth.credentials.CredentialStore.load", return_value=tokens):
            api = remote.build_api(settings, "")
        assert api.base_url == "https://configured.example"
        assert api._client._refresh_token_fn is None
