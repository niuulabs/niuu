"""Connectors: how Forge reaches a registered host's sshd.

Every connector ends in an SSH session from Forge to the host's own sshd, pinned to the
host's registered key; a connector only decides how the bytes get there.
"""

from __future__ import annotations

import asyncio
import errno
import ipaddress
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from pydantic import JsonValue

from niuu.observability import get_observability
from volundr.adapters.outbound.host_checks import LEAVE_HOOKS_DIR
from volundr.domain.compute_hosts import (
    HostConnectionStatus,
    HostConnector,
    HostDisconnectedError,
    HostHealth,
    HostJoinSetup,
    HostPlatform,
    RegisteredHost,
    RegisteredHostRepository,
    SshDial,
)
from volundr.domain.services.compute_hosts import ComputeHostError, validate_host_public_key

logger = logging.getLogger(__name__)


class DirectSshConnector(HostConnector):
    """Forge dials the host's address: the host is reachable from Forge's network.

    ``allowed_sources`` are the addresses or CIDRs Forge's connections come from; they become
    the ``from=`` option on Forge's key on the host. An empty list is the operator's explicit
    decision to accept Forge's key from anywhere.
    """

    def __init__(
        self,
        *,
        allowed_sources: list[str],
        ssh_port: int = 22,
        permit_open: list[str] | None = None,
        permit_listen: list[str] | None = None,
    ) -> None:
        if not 1 <= ssh_port <= 65535:
            raise ValueError("ssh_port must be between 1 and 65535")
        for source in allowed_sources:
            ipaddress.ip_network(source, strict=False)
        self._sources = tuple(allowed_sources)
        self._port = ssh_port
        self._permit_open = tuple(permit_open or ())
        self._permit_listen = tuple(permit_listen or ())

    @property
    def kind(self) -> str:
        return "direct_ssh"

    @property
    def label(self) -> str:
        return "Forge connects over SSH"

    def join_setup(self, host: RegisteredHost) -> HostJoinSetup:
        options = ["restrict", "port-forwarding"]
        if self._sources:
            options.append(f'from="{",".join(self._sources)}"')
        options.extend(f'permitopen="{target}"' for target in self._permit_open)
        options.extend(f'permitlisten="{target}"' for target in self._permit_listen)
        return HostJoinSetup(kind=self.kind, authorized_key_options=",".join(options))

    @property
    def platforms(self) -> frozenset[HostPlatform]:
        # Its setup is only Forge's line in authorized_keys, which any OpenSSH reads.
        return frozenset(HostPlatform)

    async def admit(
        self, host: RegisteredHost, offered: Mapping[str, JsonValue]
    ) -> dict[str, JsonValue]:
        address = str(offered.get("address") or "").strip()
        if not address:
            raise ValueError(
                "The joining host reported no address; join.sh reports the source address "
                "of its default route. Check the host's network"
            )
        ipaddress.ip_address(address)
        return {"address": address, "port": self._port}

    def dial(self, host: RegisteredHost) -> SshDial:
        return SshDial(address=str(host.connection["address"]), port=int(host.connection["port"]))

    async def status(self, host: RegisteredHost) -> HostConnectionStatus:
        # Forge opens a connection per operation; whether it can is what the last check saw.
        dial = self.dial(host)
        reachable = host.health is not HostHealth.UNREACHABLE
        return HostConnectionStatus(
            connected=reachable and host.health is not HostHealth.PENDING,
            target=f"{dial.address}:{dial.port}",
            since=host.last_checked_at,
            last_error=None if reachable else "; ".join(host.findings) or None,
        )

    async def revoke(self, host: RegisteredHost) -> None:
        """Nothing on Forge's side admits a direct host; its key is removed over SSH."""
        return None


#: The connection-data field that holds a reverse-SSH host's port on the endpoint's loopback.
TUNNEL_PORT = "tunnel_port"
#: The unit that keeps the host's tunnel, and the file it needs to start: the endpoint's
#: pinned host key. niuu-host-leave removes the file, so a restart after that does nothing.
CONNECT_UNIT = "niuu-host-connect.service"
CONNECT_UNIT_PATH = f"/etc/systemd/system/{CONNECT_UNIT}"
CONNECT_KNOWN_HOSTS = "/etc/niuu/connect_known_hosts"
CONNECT_LEAVE_HOOK = f"{LEAVE_HOOKS_DIR}/connect.sh"
#: join.sh's link to sshd's own Ed25519 key: the tunnel logs in with the host's identity.
HOST_KEY_PATH = "/etc/niuu/ssh_host_ed25519_key"

_ENDPOINT = re.compile(r"^(?P<host>[A-Za-z0-9][A-Za-z0-9.-]*)(?::(?P<port>[0-9]{1,5}))?$")
_PORT_RANGE = re.compile(r"^(?P<low>[0-9]{4,5})-(?P<high>[0-9]{4,5})$")
_USER = re.compile(r"^[a-z_][a-z0-9_-]*$")
#: /proc/net/tcp: the local address 127.0.0.1 (little-endian hex) and the LISTEN state.
_LOOPBACK_HEX = "0100007F"
_TCP_LISTEN = "0A"

_UNIT = """\
[Unit]
Description=Niuu Forge connection: a reverse SSH tunnel to the Forge's connect endpoint
After=network-online.target
Wants=network-online.target
# niuu-host-leave removes this file; a restart after that does not start the tunnel again.
ConditionPathExists={known_hosts}
StartLimitIntervalSec=0

[Service]
ExecStart={command}
Restart=always
RestartSec={reconnect}

[Install]
WantedBy=multi-user.target
"""

_LEAVE_HOOK = f"""\
# Run by niuu-host-leave before it removes the unit. The tunnel is not stopped here: Forge
# may be running leave through it. Forge's removal ends the connection, and without
# {CONNECT_KNOWN_HOSTS} the unit does not start again.
systemctl disable {CONNECT_UNIT}
"""


class TunnelPortsExhaustedError(ValueError):
    """Every port of the connector's ``port_range`` is assigned or in use."""


@dataclass(frozen=True)
class _Seen:
    connected: bool
    since: datetime


class ReverseSshConnector(HostConnector):
    """The host keeps one outbound SSH connection to Forge's connect endpoint and forwards a
    port on the Forge pod's loopback to its own sshd: for hosts Forge cannot dial (NAT).

    The endpoint is an OpenSSH sidecar of the Forge pod (the volundr chart's
    ``hostConnect``) and shares Forge's loopback, so the forwarded port is reachable by
    Forge and nothing else. Its ``AuthorizedKeysCommand`` asks this connector, on a socket
    in ``runtime_dir``, for a host key's line on every connection, and records the
    connection there; revoke asks the endpoint to end it. Forge's SSH session through the
    tunnel is pinned to the host's registered key like any other.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        endpoint_host_key: str,
        port_range: str,
        runtime_dir: str = "/run/niuu-connect",
        connect_user: str = "niuu-connect",
        host_ssh_port: int = 22,
        server_alive_interval_seconds: int = 15,
        reconnect_seconds: int = 5,
        revoke_timeout_seconds: float = 10,
        revoke_check_interval_seconds: float = 0.1,
        permit_open: list[str] | None = None,
        permit_listen: list[str] | None = None,
        proc_net_tcp: str = "/proc/net/tcp",
    ) -> None:
        match = _ENDPOINT.match(endpoint)
        if match is None:
            raise ValueError(f"endpoint {endpoint!r} must be host or host:port, as hosts dial it")
        self._endpoint_host = match["host"]
        self._endpoint_port = int(match["port"] or 22)
        try:
            self._endpoint_key = validate_host_public_key(endpoint_host_key)
        except ComputeHostError as exc:
            raise ValueError(f"endpoint_host_key: {exc}") from exc
        ports = _PORT_RANGE.match(port_range)
        if ports is None or not 1024 <= int(ports["low"]) <= int(ports["high"]) <= 65535:
            raise ValueError(
                f"port_range {port_range!r} must be 'low-high' within 1024-65535, low first"
            )
        self._low, self._high = int(ports["low"]), int(ports["high"])
        for name, value in (
            ("endpoint port", self._endpoint_port),
            ("host_ssh_port", host_ssh_port),
        ):
            if not 1 <= value <= 65535:
                raise ValueError(f"{name} must be between 1 and 65535")
        if not _USER.match(connect_user):
            raise ValueError(f"connect_user {connect_user!r} is not a user name")
        if min(server_alive_interval_seconds, reconnect_seconds) < 1:
            raise ValueError("server_alive_interval_seconds and reconnect_seconds must be >= 1")
        if revoke_timeout_seconds <= 0 or revoke_check_interval_seconds <= 0:
            raise ValueError("revoke_timeout_seconds and revoke_check_interval_seconds must be > 0")
        if not Path(runtime_dir).is_dir():
            raise ValueError(
                f"runtime_dir {runtime_dir} does not exist: the host connect endpoint is not "
                "deployed with this Forge. Enable hostConnect in the volundr chart, or remove "
                "the reverse SSH connector from compute.hosts.connectors"
            )
        if not Path(proc_net_tcp).is_file():
            raise ValueError(
                f"{proc_net_tcp} is missing: the reverse SSH connector reads the tunnels' "
                "listeners from Linux's /proc in the endpoint's network namespace"
            )
        self._runtime = Path(runtime_dir)
        self._user = connect_user
        self._host_ssh_port = host_ssh_port
        self._alive = server_alive_interval_seconds
        self._reconnect = reconnect_seconds
        self._revoke_timeout = revoke_timeout_seconds
        self._revoke_interval = revoke_check_interval_seconds
        self._permit_open = tuple(permit_open or ())
        self._permit_listen = tuple(permit_listen or ())
        self._proc_net_tcp = proc_net_tcp
        self._inventory: RegisteredHostRepository | None = None
        self._revoked: set[UUID] = set()
        self._seen: dict[UUID, _Seen] = {}

    @property
    def kind(self) -> str:
        return "reverse_ssh"

    @property
    def label(self) -> str:
        return "The host connects to Forge (behind NAT)"

    @property
    def endpoint(self) -> str:
        return f"{self._endpoint_host}:{self._endpoint_port}"

    @property
    def endpoint_socket(self) -> str:
        return str(self._runtime / "keys.sock")

    def attach(self, inventory: RegisteredHostRepository) -> None:
        self._inventory = inventory

    def join_setup(self, host: RegisteredHost) -> HostJoinSetup:
        port = self._tunnel_port(host)
        # Forge's session arrives through the tunnel, on the host's own loopback.
        options = ["restrict", "port-forwarding", 'from="127.0.0.1,::1"']
        options.extend(f'permitopen="{target}"' for target in self._permit_open)
        options.extend(f'permitlisten="{target}"' for target in self._permit_listen)
        known_host = (
            self._endpoint_host
            if self._endpoint_port == 22
            else f"[{self._endpoint_host}]:{self._endpoint_port}"
        )
        command = [
            "/usr/bin/ssh",
            "-N",
            "-T",
            "-F",
            "none",
            "-i",
            HOST_KEY_PATH,
            *("-o", "IdentitiesOnly=yes"),
            *("-o", "BatchMode=yes"),
            *("-o", "ExitOnForwardFailure=yes"),
            *("-o", f"ServerAliveInterval={self._alive}"),
            *("-o", "StrictHostKeyChecking=yes"),
            *("-o", f"UserKnownHostsFile={CONNECT_KNOWN_HOSTS}"),
            *("-o", "GlobalKnownHostsFile=/dev/null"),
            *("-o", "HostKeyAlgorithms=ssh-ed25519"),
            *("-o", "CheckHostIP=no"),
            *("-o", "UpdateHostKeys=no"),
            *("-p", str(self._endpoint_port)),
            *("-R", f"127.0.0.1:{port}:127.0.0.1:{self._host_ssh_port}"),
            f"{self._user}@{self._endpoint_host}",
        ]
        return HostJoinSetup(
            kind=self.kind,
            authorized_key_options=",".join(options),
            files=(
                {
                    "path": CONNECT_KNOWN_HOSTS,
                    "content": f"{known_host} {self._endpoint_key}\n",
                    "mode": "0644",
                },
                {
                    "path": CONNECT_UNIT_PATH,
                    "content": _UNIT.format(
                        known_hosts=CONNECT_KNOWN_HOSTS,
                        command=" ".join(command),
                        reconnect=self._reconnect,
                    ),
                    "mode": "0644",
                },
                {"path": CONNECT_LEAVE_HOOK, "content": _LEAVE_HOOK, "mode": "0644"},
            ),
            commands=(
                ("systemctl", "daemon-reload"),
                ("systemctl", "enable", "--now", CONNECT_UNIT),
            ),
        )

    async def admit(
        self, host: RegisteredHost, offered: Mapping[str, JsonValue]
    ) -> dict[str, JsonValue]:
        """Assign the host a free port of ``port_range``; the address it offers is unused."""
        if isinstance(host.connection.get(TUNNEL_PORT), int):
            return dict(host.connection)
        if self._inventory is None:
            raise RuntimeError("ReverseSshConnector was not attached to the host inventory")
        assigned = {int(value) for value in await self._inventory.connection_values(TUNNEL_PORT)}
        # A port something still listens on (a tunnel that outlived its host) is not free.
        taken = assigned | self._listening()
        for port in range(self._low, self._high + 1):
            if port not in taken:
                return {TUNNEL_PORT: port}
        message = (
            f"No free tunnel port: all {self._high - self._low + 1} ports of port_range "
            f"{self._low}-{self._high} are assigned or in use. Widen port_range on the reverse "
            "SSH connector in compute.hosts.connectors, or remove hosts"
        )
        get_observability().count(
            "volundr.compute.host.port_range_full", attributes={"pool": host.pool_id}
        )
        logger.error("Host %s cannot join: %s", host.name, message)
        raise TunnelPortsExhaustedError(message)

    def dial(self, host: RegisteredHost) -> SshDial:
        return SshDial(address="127.0.0.1", port=self._tunnel_port(host))

    async def status(self, host: RegisteredHost) -> HostConnectionStatus:
        port = host.connection.get(TUNNEL_PORT)
        if not isinstance(port, int):
            return HostConnectionStatus(
                connected=False,
                target=f"through {self.endpoint}",
                last_error="The host has no tunnel port; remove it and join it again",
            )
        connected = port in self._listening()
        seen = self._observe(host, port, connected)
        return HostConnectionStatus(
            connected=connected,
            target=f"127.0.0.1:{port} through {self.endpoint}",
            since=seen.since,
            last_error=None if connected else self._down(port),
        )

    async def require_connection(self, host: RegisteredHost) -> None:
        status = await self.status(host)
        if not status.connected:
            raise HostDisconnectedError(status.last_error or "The host's tunnel is down")

    def endpoint_key_options(self, host: RegisteredHost, user: str) -> str | None:
        port = host.connection.get(TUNNEL_PORT)
        if user != self._user or host.id in self._revoked or not isinstance(port, int):
            return None
        # restrict: no shell, PTY, agent, X11 or local forwarding of its own; the endpoint's
        # sshd also refuses -L (PermitOpen none) for everyone. A host listens on its own
        # loopback port and nowhere else.
        return (
            f'restrict,port-forwarding,permitlisten="127.0.0.1:{port}",command="/usr/sbin/nologin"'
        )

    async def revoke(self, host: RegisteredHost) -> None:
        """Stop admitting the key at once, then have the endpoint end its connections."""
        self._revoked.add(host.id)
        self._seen.pop(host.id, None)
        record = self._runtime / "conns" / str(host.id)
        if not record.exists():
            return
        self._request_end(host)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._revoke_timeout
        while record.exists():
            if loop.time() >= deadline:
                self._revoke_failed(
                    host,
                    f"The connect endpoint did not end host {host.name}'s connection within "
                    f"revoke_timeout_seconds={self._revoke_timeout:g}; see the host-connect "
                    "container's log",
                )
            await asyncio.sleep(self._revoke_interval)
        logger.info("Ended host %s's connection to the connect endpoint", host.name)

    def _request_end(self, host: RegisteredHost) -> None:
        # The endpoint's reaper holds the pipe open; with no reader the open fails at once.
        try:
            fd = os.open(self._runtime / "revoke", os.O_WRONLY | os.O_NONBLOCK)
        except OSError as exc:
            if exc.errno not in {errno.ENXIO, errno.ENOENT}:
                raise
            self._revoke_failed(
                host,
                f"The connect endpoint's reaper is not running ({self._runtime / 'revoke'}: "
                f"{os.strerror(exc.errno)}), so host {host.name}'s connection cannot be ended; "
                "restart the Forge pod's host-connect container",
            )
        try:
            os.write(fd, f"{host.id}\n".encode())
        finally:
            os.close(fd)

    def _revoke_failed(self, host: RegisteredHost, message: str) -> None:
        get_observability().count(
            "volundr.compute.host.revoke_failures", attributes={"pool": host.pool_id}
        )
        logger.error(message)
        raise RuntimeError(message)

    def _observe(self, host: RegisteredHost, port: int, connected: bool) -> _Seen:
        previous = self._seen.get(host.id)
        if previous is not None and previous.connected == connected:
            return previous
        seen = _Seen(connected=connected, since=datetime.now(UTC))
        self._seen[host.id] = seen
        if previous is not None and previous.connected and not connected:
            get_observability().count(
                "volundr.compute.host.disconnects", attributes={"pool": host.pool_id}
            )
            logger.error("Host %s disconnected: %s", host.name, self._down(port))
        return seen

    def _down(self, port: int) -> str:
        return (
            f"The host's tunnel to {self.endpoint} is down: nothing listens on 127.0.0.1:{port}. "
            f"{CONNECT_UNIT} on the host reconnects it"
        )

    def _tunnel_port(self, host: RegisteredHost) -> int:
        port = host.connection.get(TUNNEL_PORT)
        if not isinstance(port, int):
            raise HostDisconnectedError(
                f"Host {host.name} has no tunnel port; remove it and join it again"
            )
        return port

    def _listening(self) -> set[int]:
        """Ports something listens on at 127.0.0.1 in this network namespace."""
        ports: set[int] = set()
        with open(self._proc_net_tcp) as table:
            for line in table.readlines()[1:]:
                fields = line.split()
                address, _, port = fields[1].partition(":")
                if address == _LOOPBACK_HEX and fields[3] == _TCP_LISTEN:
                    ports.add(int(port, 16))
        return ports
