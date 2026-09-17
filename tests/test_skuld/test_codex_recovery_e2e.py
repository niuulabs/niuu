"""Opt-in native Codex process-loss recovery, with conversation recall evidence.

Run explicitly (uses real provider tokens):

    FORGE_LIVE_CLI=1 pytest tests/test_skuld/test_codex_recovery_e2e.py -m live_cli --no-cov

FORGE_CODEX_MODEL defaults to gpt-6-astra. FORGE_CODEX_RECOVERY_EVIDENCE can
select an evidence JSON path; otherwise the artifact stays in pytest's workspace.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from skuld.transports.codex import resolve_codex_cli
from skuld.transports.codex_ws import CodexWebSocketTransport

pytestmark = [pytest.mark.e2e, pytest.mark.live_cli]


@pytest.fixture
def codex_recovery_preflight() -> str:
    if os.environ.get("FORGE_LIVE_CLI") != "1":
        pytest.skip("set FORGE_LIVE_CLI=1 to opt into real Codex provider tests")
    binary = resolve_codex_cli()
    if not shutil.which(binary):
        pytest.fail("Codex recovery prerequisite failed: Codex CLI unavailable")
    version = subprocess.run(
        [binary, "--version"], capture_output=True, text=True, timeout=30, check=False
    )
    if version.returncode:
        pytest.fail("Codex recovery prerequisite failed: Codex version command failed")
    if not (os.environ.get("OPENAI_API_KEY") or os.environ.get("CODEX_API_KEY")):
        auth = subprocess.run(
            [binary, "login", "status"], capture_output=True, text=True, timeout=30, check=False
        )
        if auth.returncode:
            pytest.fail("Codex recovery prerequisite failed: authenticate the Codex CLI first")
    return version.stdout.strip()


async def test_native_process_loss_resumes_exact_thread_and_remembers_nonce(
    tmp_path, codex_recovery_preflight
):
    workspace = tmp_path / "isolated-codex-recovery"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True, timeout=30)
    (workspace / "AGENTS.md").write_text(
        "This is an isolated conversation recovery test. Follow the user's concise "
        "instructions. Do not use tools, write files, or spawn agents.\n"
    )
    model = os.environ.get("FORGE_CODEX_MODEL", "gpt-6-astra")
    turn_timeout = float(os.environ.get("FORGE_CODEX_LIVE_TIMEOUT_SECONDS", "240"))
    artifact = Path(
        os.environ.get("FORGE_CODEX_RECOVERY_EVIDENCE", str(tmp_path / "native-recovery-live.json"))
    )
    transport = CodexWebSocketTransport(str(workspace), model=model, reasoning_effort="xhigh")
    frames: list[dict] = []
    completed = asyncio.Event()
    evidence = {
        "started_at": datetime.now(UTC).isoformat(),
        "model": model,
        "cli_version": codex_recovery_preflight,
        "workspace": str(workspace),
        "passed": False,
        "frames": frames,
    }

    async def capture(frame):
        frames.append(frame)
        if frame.get("type") == "result":
            completed.set()

    async def prompt(text, message_id):
        after = len(frames)
        completed.clear()
        await asyncio.wait_for(
            transport.send_message(text, msg_id=message_id, request_id=message_id),
            timeout=turn_timeout,
        )
        await asyncio.wait_for(completed.wait(), timeout=turn_timeout)
        turn_frames = frames[after:]
        results = [frame for frame in turn_frames if frame.get("type") == "result"]
        assert len(results) == 1, "Each request must have exactly one terminal result"
        assert results[0].get("stop_reason") == "end_turn", results[0]
        assert not results[0].get("is_error"), results[0]
        assert any(
            frame.get("type") == "user_consumed" and frame.get("msg_id") == message_id
            for frame in turn_frames
        ), "Codex must acknowledge consumption of this specific request"
        assert not any(
            block.get("type") == "tool_use"
            for frame in turn_frames
            for block in frame.get("message", {}).get("content", [])
            if isinstance(block, dict)
        ), "Recall must come from conversation history, not a tool"
        return "".join(
            frame.get("delta", {}).get("text", "")
            for frame in turn_frames
            if frame.get("type") == "content_block_delta"
        ).strip()

    transport.on_event(capture)
    try:
        await asyncio.wait_for(transport.start(), timeout=120)
        assert transport._process is not None and transport._ws is not None
        assert transport.is_alive and transport.session_id
        original_thread = transport.session_id
        original_process = transport._process
        assert original_process is not None
        evidence["original_thread_id"] = original_thread
        evidence["original_process_id"] = original_process.pid
        nonce = f"forge-recall-{uuid4().hex}"
        evidence["nonce"] = nonce
        first = await prompt(
            f"Remember this exact secret token for my next message: {nonce}. "
            "Keep it only in this conversation. Do not use tools or write any files. "
            "Reply with exactly Stored.",
            "recovery-store",
        )
        evidence["first_reply"] = first
        assert first == "Stored."
        assert not transport.is_turn_active

        # This PID comes only from this test's transport, never process discovery.
        original_process.terminate()
        await asyncio.wait_for(original_process.wait(), timeout=30)
        if transport._receive_task:
            await asyncio.wait_for(transport._receive_task, timeout=30)
        evidence["original_process_exit_code"] = original_process.returncode
        assert original_process.returncode is not None
        assert not transport.is_alive

        await asyncio.wait_for(transport.start(), timeout=120)
        assert transport.is_alive
        assert transport.session_id == original_thread
        assert transport._process is not original_process
        assert transport._process is not None
        evidence["resumed_thread_id"] = transport.session_id
        evidence["resumed_process_id"] = transport._process.pid
        second = await prompt(
            "Reply with only the exact secret token I asked you to remember earlier. "
            "Do not use tools or consult files.",
            "recovery-recall",
        )
        evidence["recall_reply"] = second
        assert second == nonce, "The resumed conversation must retain the original request"
        assert transport.session_id == original_thread
        evidence["passed"] = True
    except BaseException as exc:
        evidence["failure"] = {"type": type(exc).__name__, "detail": str(exc)}
        raise
    finally:
        try:
            await asyncio.wait_for(transport.stop(), timeout=30)
        finally:
            evidence["finished_at"] = datetime.now(UTC).isoformat()
            evidence["transport_stopped"] = not transport.is_alive
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text(json.dumps(evidence, indent=2) + "\n")
