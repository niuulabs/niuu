"""Reusable helpers for CLI-backed executor runtimes."""

from __future__ import annotations

import asyncio
import logging

from niuu.ports.cli.transport import CLITransport

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


class CliTurnRunner:
    """Serialize prompt execution over a shared CLI transport."""

    def __init__(self, transport: CLITransport) -> None:
        self._transport = transport
        self._pending_responses: dict[str, asyncio.Future[str]] = {}
        self._execute_lock = asyncio.Lock()

    @property
    def pending_responses(self) -> dict[str, asyncio.Future[str]]:
        """Expose currently pending prompt futures."""
        return self._pending_responses

    @property
    def execute_lock(self) -> asyncio.Lock:
        """Expose the shared execution lock."""
        return self._execute_lock

    async def cancel_pending(self) -> None:
        """Cancel and clear all pending prompt futures."""
        for future in self._pending_responses.values():
            if future.done():
                continue
            future.cancel()
        self._pending_responses.clear()

    async def run_prompt(self, prompt: str, request_id: str) -> str:
        """Execute *prompt* on the transport and collect the final text."""
        async with self._execute_lock:
            return await self._run_prompt_locked(prompt, request_id)

    async def _run_prompt_locked(self, prompt: str, request_id: str) -> str:
        loop = asyncio.get_running_loop()
        result_future: asyncio.Future[str] = loop.create_future()
        self._pending_responses[request_id] = result_future

        collected_text: list[str] = []
        original_callback = self._transport.event_callback

        async def capture_event(data: dict) -> None:
            event_type = data.get("type", "")

            if event_type == "assistant":
                self._capture_assistant_text(data, collected_text)
            elif event_type == "content_block_delta":
                self._capture_delta_text(data, collected_text)

            if event_type == "result":
                result_text = data.get("result", "")
                if isinstance(result_text, str) and result_text:
                    collected_text.clear()
                    collected_text.append(result_text)
                if not result_future.done():
                    result_future.set_result("\n".join(collected_text) if collected_text else "")

            if original_callback is None:
                return
            await original_callback(data)

        self._transport.on_event(capture_event)

        try:
            await self._transport.send_message(prompt)
            return await result_future
        finally:
            self._transport.on_event(original_callback)
            self._pending_responses.pop(request_id, None)

    def _capture_assistant_text(self, data: dict, collected_text: list[str]) -> None:
        message = data.get("message", {})
        if isinstance(message, dict):
            content = message.get("content", "")
            if isinstance(content, str) and content:
                collected_text.append(content)
            return

        content = data.get("content", "")
        if isinstance(content, str) and content:
            collected_text.append(content)

    def _capture_delta_text(self, data: dict, collected_text: list[str]) -> None:
        delta = data.get("delta", {})
        if not isinstance(delta, dict):
            return
        text = delta.get("text", "")
        if isinstance(text, str) and text:
            collected_text.append(text)
