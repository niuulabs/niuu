"""Compute the runtime instances this host offers when it joins a Guild.

Kept separate from ``cli.app`` so it can be unit tested without building the
whole Typer app, and separate from ``cli.api.guild`` since it reasons about
CLI configuration (mode, server), not the Guild HTTP protocol.
"""

from __future__ import annotations

from cli.api.guild import OfferedInstance
from cli.config import CLISettings


def offered_instances_for_host(settings: CLISettings) -> list[OfferedInstance]:
    """The instance(s) this host's running platform offers to a Guild it joins.

    Mini/docker mode runs one Volundr (Forge) API behind ``server.port``.
    ``external_host`` is what other machines on the LAN can actually reach;
    an unset ``external_host`` falls back to the bind host, which is only
    reachable from a Guild running on this same machine (see the "no TLS for
    mini/docker" scope note in docs/operator/joining-machines.md — this is a
    plain-http LAN URL, same as every other instance registered today).
    """
    reachable_host = settings.server.external_host or settings.server.host
    base_url = f"http://{reachable_host}:{settings.server.port}"
    return [OfferedInstance(kind="volundr", base_url=base_url)]
