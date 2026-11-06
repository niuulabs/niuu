"""Command line: ``python -m volundr.guest_agent {once|serve} --config PATH``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence

from volundr.guest_agent.agent import GuestAgent, run_once
from volundr.guest_agent.config import AgentConfig, load_config
from volundr.guest_agent.errors import GuestAgentError
from volundr.guest_agent.service import serve, stop_on_signals
from volundr.guest_agent.signers import build_signer

PROGRAM = "python -m volundr.guest_agent"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM, description="Fetch a VM guest's session credentials from Forge."
    )
    parser.add_argument(
        "mode",
        choices=("once", "serve"),
        help="once: fetch and write, then exit; serve: also refresh and serve the metadata API",
    )
    parser.add_argument("--config", required=True, help="path to the agent's JSON config")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = load_config(args.config)
        asyncio.run(_run(args.mode, config))
    except GuestAgentError as exc:
        print(f"niuu-guest-agent: error: {exc}", file=sys.stderr)
        return 1
    return 0


async def _run(mode: str, config: AgentConfig) -> None:
    signer = build_signer(
        config.auth,
        allocation_id=config.allocation_id,
        timeout_seconds=config.request_timeout_seconds,
    )
    agent = GuestAgent(config, signer)
    try:
        if mode == "once":
            await run_once(agent)
            return
        stop = asyncio.Event()
        stop_on_signals(stop)
        await serve(agent, stop)
    finally:
        await agent.close()
