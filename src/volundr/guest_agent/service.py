"""The ``serve`` mode: initial fetch, listener, refresh loop, clean shutdown."""

from __future__ import annotations

import asyncio
import logging
import signal

from volundr.guest_agent.agent import CredentialState, GuestAgent, run_refresh_loop
from volundr.guest_agent.errors import GuestAgentError
from volundr.guest_agent.server import MetadataServer

logger = logging.getLogger("volundr.guest_agent")


async def serve(agent: GuestAgent, stop: asyncio.Event) -> None:
    """Run until ``stop`` is set.

    The first fetch must succeed (the process exits non-zero otherwise, like
    ``once``), so ``/niuu/v1/credentials`` never answers without data. Only
    later refreshes are retried.
    """
    config = agent.config
    state = CredentialState(clock=agent.now)
    first = await agent.fetch_and_write()
    state.record_success(first)
    server = MetadataServer(
        state, config.listen, request_timeout_seconds=config.request_timeout_seconds
    )
    try:
        await server.start()
    except OSError as exc:
        raise GuestAgentError(
            f"cannot listen on {config.listen.address}:{config.listen.port}: {exc}; the runtime "
            "must add the address to the loopback interface before the agent starts"
        ) from exc
    logger.info("serving credentials on %s:%s", config.listen.address, server.port)
    try:
        await run_refresh_loop(agent, state, first, stop)
    finally:
        await server.stop()


def stop_on_signals(stop: asyncio.Event) -> None:
    """Set ``stop`` on SIGTERM or SIGINT."""
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)
