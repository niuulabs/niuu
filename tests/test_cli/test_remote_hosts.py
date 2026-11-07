"""Tests for cli.services.remote_hosts and cli.api.compute_hosts (`niuu remote`)."""

from __future__ import annotations

import io
import shlex
import subprocess
from typing import Any

import httpx
import pytest
import respx

from cli.api.client import APIClient
from cli.api.compute_hosts import HOSTS_PATH, INSTANCES_PATH, ComputeHostsAPI, ForgeAPIError
from cli.services import remote_hosts
from cli.services.remote_hosts import (
    RemoteError,
    SshTarget,
    find_host,
    follow_join,
    join_steps,
    leave_script,
    mint_body,
    new_host,
    parse_target,
    pick_offer,
    remote_sh,
    resolve_forge,
    run_streaming,
    step_line,
    wait_removed,
)
from volundr.adapters.inbound.host_join_script import HOST_CONFIG_PATH
from volundr.adapters.outbound.host_checks import LEAVE_PATH

BASE = "http://niuu.test"


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
    }
    host.update(overrides)
    return host


class FakeAPI:
    """Answers the overview with the next host state each read."""

    def __init__(self, states: list[list[dict[str, Any]]]) -> None:
        self.states = states
        self.reads = 0
        self.slots_set: list[tuple[str, int]] = []

    async def overview(self, instance_id: str) -> dict[str, Any]:
        assert instance_id == "f1"
        index = min(self.reads, len(self.states) - 1)
        self.reads += 1
        return {"hosts": self.states[index]}

    async def set_slots(self, instance_id: str, host_id: str, slots: int) -> dict[str, Any]:
        self.slots_set.append((host_id, slots))
        return {}


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


# ── the target ──────────────────────────────────────────────────────────────


class TestParseTarget:
    def test_plain_destination_and_alias(self) -> None:
        assert parse_target("admin@gpu-box") == SshTarget("admin@gpu-box")
        assert parse_target(" workbox ", port=2222, jump="bastion") == SshTarget(
            "workbox", port=2222, jump="bastion"
        )

    def test_ssh_url(self) -> None:
        assert parse_target("ssh://admin@gpu-box:2222") == SshTarget("admin@gpu-box", port=2222)
        assert parse_target("ssh://gpu-box/", port=22) == SshTarget("gpu-box", port=22)

    @pytest.mark.parametrize(
        ("raw", "jump", "message"),
        [
            ("", "", "empty"),
            ("-oProxyCommand=evil", "", "starts with '-'"),
            ("gpu-box", "-oX", "jump host"),
            ("gpu box", "", "no spaces"),
            ("ssh://gpu-box/path", "", "Not an ssh://"),
            ("ssh://", "", "Not an ssh://"),
        ],
    )
    def test_refuses(self, raw: str, jump: str, message: str) -> None:
        with pytest.raises(RemoteError, match=message):
            parse_target(raw, jump=jump)

    def test_conflicting_ports(self) -> None:
        with pytest.raises(RemoteError, match="names port 2222, but --port is 22"):
            parse_target("ssh://gpu-box:2222", port=22)

    def test_argv(self) -> None:
        target = SshTarget("admin@gpu-box", port=2222, jump="bastion")
        assert target.argv("echo hi", tty=True) == [
            "ssh",
            "-t",
            "-p",
            "2222",
            "-J",
            "bastion",
            "--",
            "admin@gpu-box",
            "echo hi",
        ]
        assert SshTarget("box").argv("x", tty=False) == ["ssh", "--", "box", "x"]


class TestRemoteSh:
    def test_runs_the_command_under_sh(self) -> None:
        command = "curl -fsSL -H 'Authorization: Bearer abc' https://f/join.sh | sudo sh"
        wrapped = remote_sh(command)
        assert shlex.split(wrapped) == ["sh", "-c", command]


class TestRunStreaming:
    def test_passes_output_through_indented_and_keeps_the_last_line(self) -> None:
        sink = io.BytesIO()
        result = run_streaming(
            ["sh", "-c", "printf 'one\\r\\ntwo\\n\\nprompt: '; echo oops >&2; exit 3"], out=sink
        )
        assert result.returncode == 3
        assert result.last_line == "prompt: oops"
        shown = sink.getvalue().decode()
        assert "  one\r\n  two\n" in shown
        assert "  prompt: " in shown

    def test_last_line_without_a_trailing_newline(self) -> None:
        result = run_streaming(["sh", "-c", "printf 'done'"], out=io.BytesIO())
        assert result == remote_hosts.RunResult(returncode=0, last_line="done")

    def test_missing_binary_is_loud(self) -> None:
        with pytest.raises(RemoteError, match="Install OpenSSH"):
            run_streaming(["niuu-no-such-ssh-binary"], out=io.BytesIO())


class TestLeaveScript:
    def _run(
        self, tmp_path, config: str | None, host_id: str = "h1"
    ) -> subprocess.CompletedProcess:
        script = leave_script(host_id)
        cfg = tmp_path / "compute-host.env"
        if config is not None:
            cfg.write_text(config)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "sudo").write_text('#!/bin/sh\nexec "$@"\n')
        (bin_dir / "sudo").chmod(0o755)
        leave = tmp_path / "leave"
        leave.write_text("#!/bin/sh\necho left\n")
        leave.chmod(0o755)
        script = script.replace(shlex.quote(HOST_CONFIG_PATH), shlex.quote(str(cfg)))
        script = script.replace(shlex.quote(LEAVE_PATH), shlex.quote(str(leave)))
        return subprocess.run(
            ["sh", "-c", script],
            capture_output=True,
            text=True,
            env={"PATH": f"{bin_dir}:/usr/bin:/bin"},
            check=False,
        )

    def test_already_clean_is_a_no_op(self, tmp_path) -> None:
        done = self._run(tmp_path, None)
        assert done.returncode == 0
        assert "already removed" in done.stdout

    def test_runs_leave_for_this_host(self, tmp_path) -> None:
        done = self._run(tmp_path, "HOST_ID='h1'\n")
        assert done.returncode == 0
        assert done.stdout.strip() == "left"

    def test_refuses_another_host(self, tmp_path) -> None:
        done = self._run(tmp_path, "HOST_ID='h2'\n")
        assert done.returncode == 1
        assert "joined as host h2, not h1" in done.stderr

    def test_names_the_real_paths(self) -> None:
        script = leave_script("h1")
        assert HOST_CONFIG_PATH in script
        assert LEAVE_PATH in script


# ── Forge: which Forge, which host ──────────────────────────────────────────


FORGES = [
    {"id": "f1", "slug": "ymir", "name": "Ymir"},
    {"id": "f2", "slug": "valhalla", "name": "Valhalla"},
]


class TestResolveForge:
    def test_by_id_slug_or_name(self) -> None:
        assert resolve_forge(FORGES, "f2")["id"] == "f2"
        assert resolve_forge(FORGES, "ymir")["id"] == "f1"
        assert resolve_forge(FORGES, "Valhalla")["id"] == "f2"

    def test_only_one_needs_no_name(self) -> None:
        assert resolve_forge(FORGES[:1], "")["id"] == "f1"

    def test_several_need_a_name(self) -> None:
        with pytest.raises(RemoteError, match="pick one with --forge: ymir \\(f1\\)"):
            resolve_forge(FORGES, "")

    def test_none_visible(self) -> None:
        with pytest.raises(RemoteError, match="no enabled Forge"):
            resolve_forge([], "")

    def test_unknown(self) -> None:
        with pytest.raises(RemoteError, match="No Forge 'asgard'"):
            resolve_forge(FORGES, "asgard")

    def test_ambiguous_name(self) -> None:
        twins = [{"id": "a", "name": "Forge"}, {"id": "b", "name": "Forge"}]
        with pytest.raises(RemoteError, match="names several Forges"):
            resolve_forge(twins, "Forge")


OVERVIEW = {
    "runtimes": [
        {"execution": "host-podman", "allowed": True, "refusal": None},
        {"execution": "host-gpu", "allowed": False, "refusal": "needs a GPU profile"},
    ],
    "connectors": [{"binding_id": "direct", "kind": "ssh"}],
}


class TestMintBody:
    def test_takes_the_only_offers(self) -> None:
        body = mint_body(
            OVERVIEW, execution="", connector="", tags=["gpu"], slots=None, isolation=""
        )
        assert body == {"execution": "host-podman", "connector": "direct", "tags": ["gpu"]}

    def test_passes_slots_and_isolation_only_when_given(self) -> None:
        body = mint_body(OVERVIEW, execution="x", connector="y", tags=[], slots=4, isolation="none")
        assert body == {
            "execution": "x",
            "connector": "y",
            "tags": [],
            "slots": 4,
            "isolation": "none",
        }

    def test_no_allowed_runtime_names_the_refusals(self) -> None:
        overview = {**OVERVIEW, "runtimes": OVERVIEW["runtimes"][1:]}
        with pytest.raises(RemoteError, match="no runtime for hosts \\(host-gpu: needs a GPU"):
            mint_body(overview, execution="", connector="", tags=[], slots=None, isolation="")

    def test_several_connectors_need_the_flag(self) -> None:
        with pytest.raises(RemoteError, match="pick one with --connector: a, b"):
            pick_offer("connector", "--connector", "", ["a", "b"], [])

    def test_no_connector(self) -> None:
        with pytest.raises(RemoteError, match="offers no connector for hosts$"):
            pick_offer("connector", "--connector", "", [], [])


class TestFindHost:
    def test_by_id_then_name(self) -> None:
        hosts = [_host(), _host(id="h2", name="h1")]
        assert find_host(hosts, "h1")["id"] == "h1"
        assert find_host(hosts, "gpu-box")["id"] == "h1"

    def test_unknown_lists_names(self) -> None:
        with pytest.raises(RemoteError, match="no host 'nope'. Hosts: gpu-box"):
            find_host([_host()], "nope")

    def test_ambiguous_name(self) -> None:
        with pytest.raises(RemoteError, match="use the id: h1, h2"):
            find_host([_host(), _host(id="h2")], "gpu-box")


class TestNewHost:
    def test_the_one_added(self) -> None:
        assert new_host({"h0"}, [_host(id="h0"), _host()], "box")["id"] == "h1"

    def test_none_added_is_a_failed_join(self) -> None:
        with pytest.raises(RemoteError, match="did not register a host"):
            new_host({"h1"}, [_host()], "box")

    def test_several_added_is_ambiguous(self) -> None:
        with pytest.raises(RemoteError, match="ambiguous"):
            new_host(set(), [_host(), _host(id="h2")], "box")


# ── following a host ─────────────────────────────────────────────────────────


class TestJoinSteps:
    def _statuses(self, host: dict[str, Any]) -> dict[str, str]:
        return {step.id: step.status for step in join_steps(host)}

    def test_just_joined(self) -> None:
        assert self._statuses(_host()) == {
            "joined": "done",
            "connected": "now",
            "preparing": "todo",
            "audit": "todo",
            "capacity": "todo",
            "ready": "todo",
        }

    def test_connected_and_preparing(self) -> None:
        host = _host(connection={"connected": True})
        assert self._statuses(host)["connected"] == "done"
        assert self._statuses(host)["preparing"] == "now"

    def test_unreachable(self) -> None:
        host = _host(state="unreachable", connection={"connected": False, "last_error": "refused"})
        steps = {step.id: step for step in join_steps(host)}
        assert steps["connected"].status == "failed"
        assert steps["connected"].detail == "refused"
        assert join_steps(_host(state="unreachable", connection=None))[1].detail == (
            "Forge cannot reach the host"
        )

    def test_quarantined_while_preparing(self) -> None:
        host = _host(state="quarantined", health="quarantined", connection={"connected": True})
        assert self._statuses(host)["preparing"] == "failed"

    def test_audited_without_slots(self) -> None:
        host = _host(state="ready", health="healthy", connection={"connected": True})
        statuses = self._statuses(host)
        assert statuses["audit"] == "done"
        assert statuses["capacity"] == "now"
        assert statuses["ready"] == "done"

    def test_ready_with_slots(self) -> None:
        host = _host(state="ready", health="healthy", slots=6, connection={"connected": True})
        steps = {step.id: step for step in join_steps(host)}
        assert steps["capacity"].status == "done"
        assert step_line(steps["capacity"]) == "✓ capacity     6 slots"
        assert step_line(steps["ready"]) == "✓ ready        tags: gpu"
        assert join_steps(_host(tags=[]))[-1].detail == "no tags"


def _connected(**overrides: Any) -> dict[str, Any]:
    return _host(connection={"connected": True}, **overrides)


class TestFollowJoin:
    async def _follow(
        self, api: FakeAPI, *, suggested: bool = False, timeout: float = 60.0
    ) -> tuple[Any, list[str]]:
        clock = Clock()
        lines: list[str] = []
        result = await follow_join(
            api,  # type: ignore[arg-type]
            "f1",
            "h1",
            slots_suggested=suggested,
            timeout_seconds=timeout,
            poll_seconds=2.0,
            echo=lines.append,
            sleep=clock.sleep,
            clock=clock,
        )
        return result, lines

    async def test_to_ready_prints_each_step_once(self) -> None:
        api = FakeAPI(
            [
                [_host()],
                [_host()],
                [_connected()],
                [_connected(state="ready", health="healthy", slots=4)],
            ]
        )
        result, lines = await self._follow(api)
        assert result.outcome == "ready"
        assert [line.split()[1] for line in lines] == [
            "joined",
            "connected",
            "connected",
            "preparing",
            "preparing",
            "audit",
            "capacity",
            "ready",
        ]
        assert lines[1].startswith("…") and lines[2].startswith("✓")

    async def test_needs_capacity_returns_for_the_operator(self) -> None:
        api = FakeAPI([[_connected(state="pending", health="healthy")]])
        result, _ = await self._follow(api)
        assert result.outcome == "needs_capacity"
        assert api.slots_set == []

    async def test_suggested_slots_are_applied_once(self) -> None:
        waiting = _connected(state="pending", health="healthy", suggested_slots={"slots": 6})
        api = FakeAPI(
            [[waiting], [waiting], [_connected(state="ready", health="healthy", slots=6)]]
        )
        result, lines = await self._follow(api, suggested=True)
        assert result.outcome == "ready"
        assert api.slots_set == [("h1", 6)]
        assert "  set 6 slots (Forge's suggestion)" in lines

    async def test_suggested_without_a_suggestion_is_loud(self) -> None:
        api = FakeAPI([[_connected(state="pending", health="healthy")]])
        with pytest.raises(RemoteError, match="no slot suggestion.*niuu remote slots gpu-box"):
            await self._follow(api, suggested=True)

    async def test_quarantined_fails_with_findings(self) -> None:
        api = FakeAPI(
            [
                [
                    _connected(
                        state="quarantined",
                        health="quarantined",
                        findings=["nix trusted-users has alice"],
                    )
                ]
            ]
        )
        with pytest.raises(RemoteError, match="is quarantined:\n  - nix trusted-users has alice"):
            await self._follow(api)

    async def test_unreachable_fails_with_the_connection_error(self) -> None:
        api = FakeAPI(
            [
                [
                    _host(
                        state="unreachable",
                        connection={"connected": False, "last_error": "timed out"},
                    )
                ]
            ]
        )
        with pytest.raises(RemoteError, match="unreachable.*\n  - timed out"):
            await self._follow(api)

    async def test_unreachable_without_a_reason_says_so(self) -> None:
        api = FakeAPI([[_host(state="unreachable", connection=None)]])
        with pytest.raises(RemoteError, match="Forge reported no reason"):
            await self._follow(api)

    async def test_timeout_names_the_knob_and_the_step(self) -> None:
        api = FakeAPI([[_connected()]])
        with pytest.raises(RemoteError, match="still at 'preparing' after 10 s.*follow_timeout"):
            await self._follow(api, timeout=10.0)

    async def test_a_vanished_host_is_loud(self) -> None:
        api = FakeAPI([[]])
        with pytest.raises(RemoteError, match="no host 'h1'"):
            await self._follow(api)


class TestWaitRemoved:
    async def _wait(self, api: FakeAPI, timeout: float = 60.0) -> list[str]:
        clock = Clock()
        lines: list[str] = []
        await wait_removed(
            api,  # type: ignore[arg-type]
            "f1",
            "h1",
            timeout_seconds=timeout,
            poll_seconds=2.0,
            echo=lines.append,
            sleep=clock.sleep,
            clock=clock,
        )
        return lines

    async def test_waits_for_sessions_then_returns(self) -> None:
        busy = _host(state="removing", sessions=[{"slot": 0}, {"slot": 1}])
        api = FakeAPI([[busy], [busy], [_host(state="removing", sessions=[{"slot": 0}])], []])
        lines = await self._wait(api)
        assert lines == ["… removing, 2 sessions left", "… removing, 1 session left"]

    async def test_timeout_is_loud(self) -> None:
        api = FakeAPI([[_host(state="removing", sessions=[{"slot": 0}])]])
        with pytest.raises(RemoteError, match="leave_timeout_seconds.*run `niuu remote leave"):
            await self._wait(api, timeout=4.0)


# ── the API client ───────────────────────────────────────────────────────────


@pytest.fixture
def api() -> ComputeHostsAPI:
    return ComputeHostsAPI(APIClient(base_url=BASE, access_token="operator-token"))


class TestComputeHostsAPI:
    async def test_lists_forges_with_the_operator_token(self, api: ComputeHostsAPI) -> None:
        with respx.mock:
            route = respx.get(f"{BASE}{INSTANCES_PATH}").mock(
                return_value=httpx.Response(200, json=FORGES)
            )
            assert await api.forges() == FORGES
        request = route.calls.last.request
        assert request.headers["authorization"] == "Bearer operator-token"
        assert request.url.params["kind"] == "volundr"
        assert request.url.params["enabledOnly"] == "true"

    async def test_every_host_call_names_the_forge(self, api: ComputeHostsAPI) -> None:
        with respx.mock:
            overview = respx.get(f"{BASE}{HOSTS_PATH}").mock(
                return_value=httpx.Response(200, json={"hosts": []})
            )
            mint = respx.post(f"{BASE}{HOSTS_PATH}/pairing-codes").mock(
                return_value=httpx.Response(201, json={"id": "c1"})
            )
            revoke = respx.delete(f"{BASE}{HOSTS_PATH}/pairing-codes/c1").mock(
                return_value=httpx.Response(204)
            )
            patch = respx.patch(f"{BASE}{HOSTS_PATH}/h1").mock(
                return_value=httpx.Response(200, json={"slots": 3})
            )
            drain = respx.post(f"{BASE}{HOSTS_PATH}/h1/drain").mock(
                return_value=httpx.Response(200, json={"state": "removing"})
            )
            assert await api.overview("f1") == {"hosts": []}
            assert await api.mint("f1", {"execution": "x"}) == {"id": "c1"}
            assert await api.revoke("f1", "c1") is None
            assert await api.set_slots("f1", "h1", 3) == {"slots": 3}
            assert await api.drain("f1", "h1", remove=True) == {"state": "removing"}
        for route in (overview, mint, revoke, patch, drain):
            assert route.calls.last.request.url.params["instance_id"] == "f1"
        assert mint.calls.last.request.content == b'{"execution":"x"}'
        assert patch.calls.last.request.content == b'{"slots":3}'
        assert drain.calls.last.request.content == b'{"remove":true}'
        assert api.base_url == BASE

    async def test_forge_refusal_carries_its_detail(self, api: ComputeHostsAPI) -> None:
        with respx.mock:
            respx.post(f"{BASE}{HOSTS_PATH}/pairing-codes").mock(
                return_value=httpx.Response(422, json={"detail": "unknown field isolation"})
            )
            with pytest.raises(ForgeAPIError, match="422 unknown field isolation") as caught:
                await api.mint("f1", {"isolation": "none"})
        assert caught.value.status_code == 422

    async def test_a_forge_without_hosts_says_so(self, api: ComputeHostsAPI) -> None:
        with respx.mock:
            respx.get(f"{BASE}{HOSTS_PATH}").mock(
                return_value=httpx.Response(404, json={"detail": "Not Found"})
            )
            with pytest.raises(ForgeAPIError, match="configure compute.hosts") as caught:
                await api.overview("f1")
        assert caught.value.status_code == 404

    async def test_401_says_to_log_in(self, api: ComputeHostsAPI) -> None:
        with respx.mock:
            respx.get(f"{BASE}{HOSTS_PATH}").mock(return_value=httpx.Response(401, text="no token"))
            with pytest.raises(ForgeAPIError, match="Run `niuu login`"):
                await api.overview("f1")

    async def test_non_dict_error_bodies(self, api: ComputeHostsAPI) -> None:
        with respx.mock:
            respx.get(f"{BASE}{HOSTS_PATH}").mock(return_value=httpx.Response(503, json=["down"]))
            with pytest.raises(ForgeAPIError, match="503 \\['down'\\]"):
                await api.overview("f1")
            respx.get(f"{BASE}{INSTANCES_PATH}").mock(return_value=httpx.Response(502))
            with pytest.raises(ForgeAPIError, match="502 Bad Gateway"):
                await api.forges()

    async def test_unreachable_niuu_host_names_the_url(self, api: ComputeHostsAPI) -> None:
        with respx.mock:
            respx.get(f"{BASE}{INSTANCES_PATH}").mock(side_effect=httpx.ConnectError("refused"))
            with pytest.raises(ForgeAPIError, match="cannot reach http://niuu.test"):
                await api.forges()
