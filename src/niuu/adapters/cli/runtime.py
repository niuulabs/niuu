"""Reusable helpers for CLI-backed executor runtimes."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("niuu.cli.runtime")


def filter_cli_event(data: dict) -> dict | None:
    """Drop transport noise before forwarding events to higher layers."""
    msg_type = data.get("type")
    if msg_type == "keep_alive":
        return None

    if msg_type != "content_block_delta":
        return data

    delta = data.get("delta", {})
    has_content = delta.get("text") or delta.get("thinking") or delta.get("partial_json")
    if not has_content:
        logger.debug("Filtering out empty content_block_delta event")
        return None
    return data


async def drain_process_stream(stream: asyncio.StreamReader | None, label: str) -> None:
    """Read and log a stream to prevent subprocess backpressure."""
    if stream is None:
        logger.debug("drain_process_stream(%s): stream is None", label)
        return

    line_count = 0
    try:
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode().rstrip()
            if not text:
                continue
            line_count += 1
            logger.info("CLI %s: %s", label, text)
    except Exception as exc:
        logger.warning("Stream drain (%s) ended with error: %r", label, exc)
    finally:
        logger.info("drain_process_stream(%s): finished after %d lines", label, line_count)


async def stop_subprocess(process: asyncio.subprocess.Process) -> None:
    """Terminate a subprocess gracefully, killing on timeout."""
    if process.returncode is not None:
        return

    logger.info("Stopping CLI process (pid=%s)", process.pid)
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5.0)
    except TimeoutError:
        logger.warning("CLI process did not terminate (pid=%s), killing", process.pid)
        process.kill()
        await process.wait()
