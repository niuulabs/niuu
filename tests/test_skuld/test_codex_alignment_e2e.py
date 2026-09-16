"""Opt-in owned native streaming/steering proof. Uses real provider tokens."""

import asyncio
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from niuu.domain.transcript_reducer import reduce_frames
from skuld.channels import WebSocketChannel
from skuld.transports.codex_ws import CodexWebSocketTransport
from tests.test_skuld.test_codex_recovery_e2e import codex_recovery_preflight  # noqa: F401

pytestmark = [pytest.mark.e2e, pytest.mark.live_cli]


async def test_native_interleaving_live_steer_catalog_and_replay(
    tmp_path,
    codex_recovery_preflight,  # noqa: F811
):
    workspace = tmp_path / "native-alignment"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    (workspace / "AGENTS.md").write_text(
        "Isolated native acceptance test. Do not spawn agents, use network, edit files, "
        "or access anything outside this workspace. Run only the two requested shell commands.\n"
    )
    t = CodexWebSocketTransport(str(workspace), model="gpt-6-astra", reasoning_effort="xhigh")
    frames, native, public = [], [], []
    done, running = asyncio.Event(), asyncio.Event()

    async def capture_public(raw):
        public.append(json.loads(raw))

    channel = WebSocketChannel(SimpleNamespace(send_text=capture_public))
    evidence = {
        "passed": False,
        "cli_version": codex_recovery_preflight,
        "frames": frames,
        "native": native,
        "public": public,
        "started_at": datetime.now(UTC).isoformat(),
    }
    original = t._handle_server_message

    async def tap(data):
        # Native notifications from this synthetic workspace only. Never record
        # handshake/auth RPC responses or credentials.
        if "method" in data:
            native.append(data)
        await original(data)
        if (
            data.get("method") == "item/started"
            and data.get("params", {}).get("item", {}).get("type") == "commandExecution"
        ):
            running.set()

    async def capture(frame):
        frames.append(frame)
        await channel.send_event(frame)
        if frame.get("type") == "result":
            done.set()

    t._handle_server_message = tap
    t.on_event(capture)
    artifact = Path(
        os.environ.get("FORGE_CODEX_ALIGNMENT_EVIDENCE", str(tmp_path / "native-alignment.json"))
    )
    try:
        await asyncio.wait_for(t.start(), 90)
        evidence["native_thread_id"] = t.session_id
        evidence["owned_pid"] = t._process.pid
        options = await t.get_runtime_options(refresh=True)
        evidence["options"] = options
        assert "gpt-6-astra" in [m["model"] for m in options["models"]]
        await t.send_control(
            "set_runtime_options",
            options={"model": "gpt-6-astra", "effort": "xhigh", "service_tier": None},
        )
        await t.send_message(
            "This is a streaming integration test. First send a public commentary message "
            "containing BEFORE_TOOL. Then run exactly this shell command in this workspace: "
            "printf 'FIRST_OUTPUT\\n'; sleep 8. After that command completes, send another "
            "public commentary message containing BETWEEN_TOOLS. Then run exactly: "
            "printf 'SECOND_OUTPUT\\n'. Finish with a final answer containing FINAL_DONE. "
            "Do not combine the commands. Do not use any other tools or spawn agents. "
            "A short follow-up may arrive while the first command runs; incorporate it "
            "without interrupting or repeating either command.",
            msg_id="alignment-initial",
            request_id="alignment-initial",
        )
        await asyncio.wait_for(running.wait(), 180)
        assert not done.is_set(), "A tool must still be active when the steer is submitted"
        active_turn = t._current_turn_id
        evidence["steered_turn_id"] = active_turn
        await t.send_control(
            "steer",
            content=(
                "Also include STEER_ACCEPTED in the final answer. Continue the same two "
                "commands; do not repeat or interrupt them."
            ),
            msg_id="alignment-steer",
            request_id="alignment-steer",
        )
        await asyncio.wait_for(done.wait(), 240)
        results = [f for f in frames if f.get("type") == "result"]
        assert len(results) == 1 and not results[0].get("is_error"), results
        assert results[0]["turn_id"] == active_turn
        assert any(
            f.get("type") == "user_consumed" and f.get("msg_id") == "alignment-steer"
            for f in frames
        )
        completed = [d["params"]["item"] for d in native if d.get("method") == "item/completed"]
        native_text = [
            i for i in completed if i.get("type") == "agentMessage" and i.get("phase") != "analysis"
        ]
        assert len(native_text) >= 3
        assert len([i for i in completed if i.get("type") == "commandExecution"]) == 2

        def fold(values):
            return reduce_frames(
                [
                    SimpleNamespace(
                        session_id="owned",
                        seq=i + 1,
                        kind=f["type"],
                        payload=f,
                        request_id=None,
                        ts=datetime.now(UTC),
                    )
                    for i, f in enumerate(values)
                ]
            ).turns

        durable, visible = fold(frames), fold(public)
        evidence["durable_turns"], evidence["public_turns"] = durable, visible
        for turns in (durable, visible):
            parts = [p for turn in turns for p in turn["parts"] if p["type"] == "text"]
            for item in native_text:
                assert any(
                    p.get("id") == item["id"] and p["text"] == item["text"] for p in parts
                ), item
            text = "\n".join(p["text"] for p in parts)
            assert all(
                marker in text
                for marker in ("BEFORE_TOOL", "BETWEEN_TOOLS", "FINAL_DONE", "STEER_ACCEPTED")
            )
        evidence["passed"] = True
    except BaseException as exc:
        evidence["failure"] = {"type": type(exc).__name__, "detail": str(exc)}
        raise
    finally:
        await asyncio.wait_for(t.stop(), 30)
        evidence["stopped"] = not t.is_alive
        evidence["finished_at"] = datetime.now(UTC).isoformat()
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(json.dumps(evidence, indent=2) + "\n")
