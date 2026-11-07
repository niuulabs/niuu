"""Connectors: how Forge reaches a registered host's sshd.

Every connector ends in an SSH session from Forge to the host's own sshd, pinned to the
host's registered key; a connector only decides how the bytes get there.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping

from pydantic import JsonValue

from volundr.domain.compute_hosts import (
    HostConnectionStatus,
    HostConnector,
    HostHealth,
    HostJoinSetup,
    RegisteredHost,
    SshDial,
)


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
