"""Compute the runtime instances this host offers when it joins a Guild.

Kept separate from ``cli.app`` so it can be unit tested without building the
whole Typer app, and separate from ``cli.api.guild`` since it reasons about
CLI configuration (mode, server), not the Guild HTTP protocol.
"""

from __future__ import annotations

from cli.api.guild import OfferedInstance
from cli.config import CLISettings


class UnreachableHostError(ValueError):
    """Raised when this host has no address another machine can dial.

    ``server.host`` is a bind address (often ``127.0.0.1`` or ``0.0.0.0``),
    not a reachable one — silently registering it as if it were would give
    Guild and every other joined machine an instance URL that only works
    from localhost, a broken registration that looks successful. There is
    no safe fallback here: the operator must say what address this host is
    reachable at.
    """


def offered_instances_for_host(settings: CLISettings) -> list[OfferedInstance]:
    """The instance(s) this host's running platform offers to a Guild it joins.

    Mini/docker mode runs one Volundr (Forge) API behind ``server.port``.
    Requires ``server.external_host`` to be set explicitly — see
    ``UnreachableHostError``.
    """
    reachable_host = settings.server.external_host.strip()
    if not reachable_host:
        raise UnreachableHostError(
            "server.external_host is not set. Set it to this host's LAN address or "
            "hostname (what another machine on the network dials) before running "
            "`niuu join` — server.host is only a bind address, not a reachable one."
        )
    base_url = f"http://{reachable_host}:{settings.server.port}"
    return [OfferedInstance(kind="volundr", base_url=base_url)]
