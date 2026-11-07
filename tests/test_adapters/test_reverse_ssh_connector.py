"""The reverse SSH connector: ports, the host's connect unit, tunnel health and revoke."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from unittest.mock import patch

import pytest

from tests.compute_host_fakes import HostRepository, make_host, other_host_key
from volundr.adapters.inbound.host_join_script import NIUU_HOST_KEY_PATH
from volundr.adapters.outbound.host_checks import LEAVE_HOOKS_DIR
from volundr.adapters.outbound.host_connectors import (
    CONNECT_KNOWN_HOSTS,
    CONNECT_LEAVE_HOOK,
    CONNECT_UNIT,
    CONNECT_UNIT_PATH,
    HOST_KEY_PATH,
    ReverseSshConnector,
    TunnelPortsExhaustedError,
)
from volundr.domain.compute_hosts import HostDisconnectedError, HostJoinSetup

ENDPOINT_KEY = other_host_key(50)
OBSERVE = "volundr.adapters.outbound.host_connectors.get_observability"


def _listen(table, *ports, address="0100007F", state="0A"):
    lines = ["  sl  local_address rem_address   st tx_queue rx_queue"]
    lines += [
        f"   {i}: {address}:{port:04X} 00000000:0000 {state} 00000000:00000000"
        for i, port in enumerate(ports)
    ]
    table.write_text("\n".join(lines) + "\n")


@pytest.fixture
def runtime(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    table = tmp_path / "tcp"
    _listen(table)
    return run, table


def _connector(runtime, **changes):
    run, table = runtime
    kwargs = {
        "endpoint": "hosts-connect.example:2222",
        "endpoint_host_key": ENDPOINT_KEY,
        "port_range": "20000-20002",
        "runtime_dir": str(run),
        "proc_net_tcp": str(table),
        "revoke_timeout_seconds": 1,
        "revoke_check_interval_seconds": 0.01,
    }
    kwargs.update(changes)
    return ReverseSshConnector(**kwargs)


def _reverse_host(port=20000, **changes):
    return make_host(connector="reverse-ssh", connection={"tunnel_port": port}, **changes)


class TestConfiguration:
    def test_describes_itself(self, runtime):
        connector = _connector(runtime)
        assert connector.kind == "reverse_ssh"
        assert "NAT" in connector.label
        assert connector.endpoint == "hosts-connect.example:2222"
        assert connector.endpoint_socket == str(runtime[0] / "keys.sock")

    def test_an_endpoint_without_a_port_is_ssh_s(self, runtime):
        assert _connector(runtime, endpoint="hosts-connect.example").endpoint.endswith(":22")

    @pytest.mark.parametrize(
        ("changes", "message"),
        [
            ({"endpoint": "a host:22"}, "host or host:port"),
            ({"endpoint": "host:0"}, "endpoint port"),
            ({"endpoint_host_key": "ssh-rsa AAAA"}, "endpoint_host_key"),
            ({"port_range": "20000"}, "port_range"),
            ({"port_range": "20010-20000"}, "port_range"),
            ({"port_range": "80-90"}, "port_range"),
            ({"port_range": "60000-70000"}, "port_range"),
            ({"host_ssh_port": 70000}, "host_ssh_port"),
            ({"connect_user": "Root!"}, "connect_user"),
            ({"server_alive_interval_seconds": 0}, "server_alive"),
            ({"reconnect_seconds": 0}, "reconnect_seconds"),
            ({"revoke_timeout_seconds": 0}, "revoke_timeout_seconds"),
            ({"revoke_check_interval_seconds": 0}, "revoke_check_interval_seconds"),
        ],
    )
    def test_refuses_what_cannot_work(self, runtime, changes, message):
        with pytest.raises(ValueError, match=message):
            _connector(runtime, **changes)

    def test_refuses_a_forge_without_the_endpoint(self, runtime, tmp_path):
        with pytest.raises(ValueError, match="hostConnect"):
            _connector(runtime, runtime_dir=str(tmp_path / "missing"))

    def test_refuses_a_forge_without_linux_proc(self, runtime, tmp_path):
        with pytest.raises(ValueError, match="/proc"):
            _connector(runtime, proc_net_tcp=str(tmp_path / "missing"))


class TestJoinSetup:
    def test_the_tunnel_logs_in_with_the_host_key_join_links(self):
        assert HOST_KEY_PATH == NIUU_HOST_KEY_PATH
        assert CONNECT_LEAVE_HOOK.startswith(f"{LEAVE_HOOKS_DIR}/")

    def test_forges_key_is_admitted_only_from_the_hosts_loopback(self, runtime):
        setup = _connector(runtime, permit_open=["127.0.0.1:21000"]).join_setup(_reverse_host())
        assert setup.authorized_key_options == (
            'restrict,port-forwarding,from="127.0.0.1,::1",permitopen="127.0.0.1:21000"'
        )

    def test_permit_listen_is_passed_through(self, runtime):
        setup = _connector(runtime, permit_listen=["127.0.0.1:30000"]).join_setup(_reverse_host())
        assert setup.authorized_key_options.endswith('permitlisten="127.0.0.1:30000"')

    def test_the_unit_forwards_the_assigned_port_to_sshd_and_reconnects(self, runtime):
        setup = _connector(runtime, server_alive_interval_seconds=20, reconnect_seconds=7)
        setup = setup.join_setup(_reverse_host(20001))
        files = {item["path"]: item for item in setup.files}
        unit = files[CONNECT_UNIT_PATH]["content"]
        command = re.search(r"^ExecStart=(.*)$", unit, re.M)[1].split()

        assert command[0] == "/usr/bin/ssh"
        assert command[command.index("-R") + 1] == "127.0.0.1:20001:127.0.0.1:22"
        assert command[command.index("-i") + 1] == HOST_KEY_PATH
        assert command[command.index("-p") + 1] == "2222"
        assert command[-1] == "niuu-connect@hosts-connect.example"
        for option in (
            "ExitOnForwardFailure=yes",
            "ServerAliveInterval=20",
            "StrictHostKeyChecking=yes",
            f"UserKnownHostsFile={CONNECT_KNOWN_HOSTS}",
            "BatchMode=yes",
        ):
            assert option in command
        assert "Restart=always" in unit
        assert "RestartSec=7" in unit
        assert "StartLimitIntervalSec=0" in unit
        # Once leave removed the pinned key, a restart does not start the tunnel again.
        assert f"ConditionPathExists={CONNECT_KNOWN_HOSTS}" in unit
        assert setup.commands == (
            ("systemctl", "daemon-reload"),
            ("systemctl", "enable", "--now", CONNECT_UNIT),
        )

    def test_the_endpoint_key_is_pinned(self, runtime):
        files = {f["path"]: f for f in _connector(runtime).join_setup(_reverse_host()).files}
        assert files[CONNECT_KNOWN_HOSTS]["content"] == (
            f"[hosts-connect.example]:2222 {ENDPOINT_KEY}\n"
        )
        default_port = _connector(runtime, endpoint="hosts-connect.example")
        files = {f["path"]: f for f in default_port.join_setup(_reverse_host()).files}
        assert files[CONNECT_KNOWN_HOSTS]["content"].startswith("hosts-connect.example ssh-")

    def test_leave_disables_the_unit_without_stopping_it(self, runtime):
        files = {f["path"]: f for f in _connector(runtime).join_setup(_reverse_host()).files}
        hook = files[CONNECT_LEAVE_HOOK]["content"]
        commands = [line for line in hook.splitlines() if line and not line.startswith("#")]
        assert commands == [f"systemctl disable {CONNECT_UNIT}"]

    def test_the_setup_is_valid_for_join(self, runtime):
        setup = _connector(runtime).join_setup(_reverse_host())
        assert HostJoinSetup.model_validate(setup.model_dump()) == setup

    def test_a_host_without_a_port_has_no_setup(self, runtime):
        with pytest.raises(HostDisconnectedError, match="no tunnel port"):
            _connector(runtime).join_setup(make_host(connector="reverse-ssh"))


class TestAdmit:
    async def test_assigns_the_lowest_free_port(self, runtime):
        connector = _connector(runtime)
        connector.attach(HostRepository(_reverse_host(20000, host_public_key=other_host_key(1))))
        assert await connector.admit(make_host(), {"address": "10.0.0.9"}) == {"tunnel_port": 20001}

    async def test_skips_a_port_something_still_listens_on(self, runtime):
        _listen(runtime[1], 20000, 20001)
        connector = _connector(runtime)
        connector.attach(HostRepository())
        assert await connector.admit(make_host(), {}) == {"tunnel_port": 20002}

    async def test_only_loopback_listeners_count(self, runtime):
        _listen(runtime[1], 20000, address="00000000")
        _listen_extra = runtime[1].read_text() + "   9: 0100007F:4E20 00000000:0000 01 x\n"
        runtime[1].write_text(_listen_extra)
        connector = _connector(runtime)
        connector.attach(HostRepository())
        assert await connector.admit(make_host(), {}) == {"tunnel_port": 20000}

    async def test_is_idempotent(self, runtime):
        connector = _connector(runtime)
        host = _reverse_host(20002)
        assert await connector.admit(host, {}) == {"tunnel_port": 20002}

    async def test_a_full_range_refuses_the_join_naming_port_range(self, runtime, caplog):
        hosts = [
            _reverse_host(port, host_public_key=other_host_key(port)) for port in (20000, 20001)
        ]
        _listen(runtime[1], 20002)
        connector = _connector(runtime)
        connector.attach(HostRepository(*hosts))
        with patch(OBSERVE) as observe, pytest.raises(TunnelPortsExhaustedError) as raised:
            await connector.admit(make_host(), {})
        assert "port_range 20000-20002" in str(raised.value)
        assert isinstance(raised.value, ValueError)  # join turns it into a 422
        observe.return_value.count.assert_called_once_with(
            "volundr.compute.host.port_range_full", attributes={"pool": "hosts"}
        )
        assert "cannot join" in caplog.text

    async def test_needs_the_inventory(self, runtime):
        with pytest.raises(RuntimeError, match="attached"):
            await _connector(runtime).admit(make_host(), {})


class TestTunnel:
    def test_forge_dials_the_tunnel_on_its_loopback(self, runtime):
        dial = _connector(runtime).dial(_reverse_host(20001))
        assert (dial.address, dial.port, dial.proxy_command) == ("127.0.0.1", 20001, ())

    async def test_a_listening_port_is_connected(self, runtime):
        _listen(runtime[1], 20001)
        status = await _connector(runtime).status(_reverse_host(20001))
        assert status.connected
        assert status.target == "127.0.0.1:20001 through hosts-connect.example:2222"
        assert status.since is not None
        assert status.last_error is None

    async def test_a_dropped_tunnel_is_disconnected_counted_and_logged(self, runtime, caplog):
        _listen(runtime[1], 20001)
        connector = _connector(runtime)
        host = _reverse_host(20001)
        up = await connector.status(host)
        assert (await connector.status(host)).since == up.since

        _listen(runtime[1])
        with patch(OBSERVE) as observe:
            down = await connector.status(host)
            await connector.status(host)

        assert not down.connected
        assert "nothing listens on 127.0.0.1:20001" in down.last_error
        assert CONNECT_UNIT in down.last_error
        assert down.since >= up.since
        observe.return_value.count.assert_called_once_with(
            "volundr.compute.host.disconnects", attributes={"pool": "hosts"}
        )
        assert "disconnected" in caplog.text

    async def test_a_tunnel_down_when_first_seen_is_not_a_disconnect(self, runtime):
        with patch(OBSERVE) as observe:
            status = await _connector(runtime).status(_reverse_host())
        assert not status.connected
        observe.return_value.count.assert_not_called()

    async def test_a_host_without_a_port_is_disconnected(self, runtime):
        status = await _connector(runtime).status(make_host(connector="reverse-ssh"))
        assert not status.connected
        assert "no tunnel port" in status.last_error

    async def test_require_connection_raises_while_the_tunnel_is_down(self, runtime):
        connector = _connector(runtime)
        with pytest.raises(HostDisconnectedError, match="tunnel"):
            await connector.require_connection(_reverse_host())
        _listen(runtime[1], 20000)
        await connector.require_connection(_reverse_host())


class TestEndpointKey:
    def test_admits_the_host_to_its_own_loopback_port_only(self, runtime):
        options = _connector(runtime).endpoint_key_options(_reverse_host(20002), "niuu-connect")
        assert options == (
            'restrict,port-forwarding,permitlisten="127.0.0.1:20002",command="/usr/sbin/nologin"'
        )
        # authorized_keys has no "open nothing": a permitopen="none" option makes sshd
        # drop the whole line; the endpoint's sshd_config refuses -L instead.
        assert "permitopen" not in options

    def test_admits_nothing_for_another_user_or_a_host_without_a_port(self, runtime):
        connector = _connector(runtime)
        assert connector.endpoint_key_options(_reverse_host(), "root") is None
        assert connector.endpoint_key_options(make_host(), "niuu-connect") is None

    async def test_a_revoked_host_is_not_admitted_any_more(self, runtime):
        connector = _connector(runtime)
        host = _reverse_host()
        await connector.revoke(host)
        assert connector.endpoint_key_options(host, "niuu-connect") is None


class TestRevoke:
    async def test_without_a_recorded_connection_there_is_nothing_to_end(self, runtime):
        await _connector(runtime).revoke(_reverse_host())

    async def test_kills_the_live_connection_through_the_endpoint(self, runtime):
        run, _ = runtime
        host = _reverse_host()
        record = run / "conns" / str(host.id)
        record.mkdir(parents=True)
        (record / "4242-99").touch()
        os.mkfifo(run / "revoke")
        reader = os.open(run / "revoke", os.O_RDONLY | os.O_NONBLOCK)
        try:
            revoking = asyncio.create_task(_connector(runtime).revoke(host))
            requested = b""
            while not requested:
                await asyncio.sleep(0.01)
                try:
                    requested = os.read(reader, 100)
                except BlockingIOError:
                    continue
            assert not revoking.done()  # it waits for the endpoint to end the connection
            shutil.rmtree(record)  # what the endpoint's reaper does once it killed it
            await revoking
        finally:
            os.close(reader)
        assert requested == f"{host.id}\n".encode()

    async def test_an_endpoint_that_does_not_end_it_fails_loudly(self, runtime, caplog):
        run, _ = runtime
        host = _reverse_host()
        (run / "conns" / str(host.id)).mkdir(parents=True)
        os.mkfifo(run / "revoke")
        reader = os.open(run / "revoke", os.O_RDONLY | os.O_NONBLOCK)
        try:
            with patch(OBSERVE) as observe, pytest.raises(RuntimeError, match="revoke_timeout"):
                await _connector(runtime, revoke_timeout_seconds=0.05).revoke(host)
        finally:
            os.close(reader)
        observe.return_value.count.assert_called_once_with(
            "volundr.compute.host.revoke_failures", attributes={"pool": "hosts"}
        )
        assert "did not end" in caplog.text

    @pytest.mark.parametrize("make_pipe", [True, False])
    async def test_an_endpoint_without_its_reaper_fails_loudly(self, runtime, make_pipe):
        run, _ = runtime
        host = _reverse_host()
        (run / "conns" / str(host.id)).mkdir(parents=True)
        if make_pipe:
            os.mkfifo(run / "revoke")  # no reader holds it open
        connector = _connector(runtime)
        with patch(OBSERVE) as observe, pytest.raises(RuntimeError, match="reaper"):
            await connector.revoke(host)
        observe.return_value.count.assert_called_once()
        # Even so, the key is no longer admitted.
        assert connector.endpoint_key_options(host, "niuu-connect") is None

    async def test_another_error_opening_the_pipe_is_raised_as_is(self, runtime):
        run, _ = runtime
        host = _reverse_host()
        (run / "conns" / str(host.id)).mkdir(parents=True)
        (run / "revoke").mkdir()
        with pytest.raises(IsADirectoryError):
            await _connector(runtime).revoke(host)
