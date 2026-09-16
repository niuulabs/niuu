#!/usr/bin/env python3
"""Capture real PI RPC execution and optional restart evidence before adapter work.

This probes the native runtime only. It does not claim Forge database/UI acceptance.
Pass --model provider/model to include an authenticated agent turn with tool use.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path


class PiProbe:
    def __init__(self, args: argparse.Namespace, frames: list[dict]) -> None:
        self.args = args
        self.frames = frames
        self.process: asyncio.subprocess.Process | None = None
        self.sequence = 0

    async def start(self, session: str | None = None) -> None:
        root = self.args.output_dir
        command = [
            self.args.pi_bin,
            "--mode",
            "rpc",
            "--session-dir",
            str(root / "sessions"),
            "--no-extensions",
            "--no-skills",
            "--no-prompt-templates",
        ]
        if session:
            command.extend(["--session", session])
        if self.args.model:
            command.extend(["--model", self.args.model])
        env = dict(os.environ)
        if self.args.isolated:
            env["PI_CODING_AGENT_DIR"] = str(root / "isolated-agent")
        with (root / "stderr.log").open("ab") as stderr:
            self.process = await asyncio.create_subprocess_exec(
                *command,
                cwd=root / "workspace",
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=stderr,
                limit=self.args.max_frame_bytes,
            )

    async def read(self) -> dict:
        assert self.process and self.process.stdout
        # readline splits on LF only; Unicode separators inside JSON strings remain intact.
        line = await asyncio.wait_for(self.process.stdout.readline(), self.args.timeout)
        if not line:
            raise RuntimeError("PI closed stdout; inspect stderr.log")
        event = json.loads(line)
        if not isinstance(event, dict):
            raise ValueError("PI returned a non-object frame")
        self.frames.append(event)
        return event

    async def command(self, kind: str, **payload: object) -> dict:
        assert self.process and self.process.stdin
        self.sequence += 1
        request_id = f"probe-{self.sequence}"
        frame = {"id": request_id, "type": kind, **payload}
        self.process.stdin.write((json.dumps(frame) + "\n").encode())
        await self.process.stdin.drain()
        while True:
            event = await self.read()
            if event.get("type") != "response" or event.get("id") != request_id:
                continue
            if event.get("success") is not True:
                raise RuntimeError(f"PI {kind} rejected: {event.get('error')}")
            return event.get("data", {})

    async def stop(self) -> None:
        if not self.process or self.process.returncode is not None:
            return
        self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), self.args.timeout)
        except TimeoutError:
            self.process.kill()
            await self.process.wait()


def assert_agent_turn(events: list[dict]) -> None:
    completions = [e for e in events if e.get("type") == "agent_end"]
    if not completions:
        raise AssertionError("No agent_end was captured")
    messages = completions[-1].get("messages", [])
    assistants = [m for m in messages if m.get("role") == "assistant"]
    if not assistants:
        raise AssertionError("No completed assistant message was captured")
    errors = [
        m.get("errorMessage") or m.get("stopReason")
        for m in assistants
        if m.get("stopReason") in ("error", "aborted")
    ]
    if errors:
        raise AssertionError(f"PI agent failed: {errors}")
    if not any(e.get("type") == "tool_execution_end" for e in events):
        raise AssertionError("The agent did not execute a tool")
    if not any(e.get("type") == "message_update" for e in events):
        raise AssertionError("The agent did not stream message updates")


async def probe(args: argparse.Namespace) -> dict:
    root = args.output_dir.resolve()
    args.output_dir = root
    (root / "workspace").mkdir(parents=True, exist_ok=True)
    frames: list[dict] = []
    runtime = PiProbe(args, frames)
    result = {
        "native_rpc": False,
        "restart_replay": False,
        "agent_turn": False,
        "forge_database_replay": False,
        "ios_replay": False,
    }
    try:
        await runtime.start()
        before = await runtime.command("get_state")
        execution = await runtime.command(
            "bash", command="printf 'PI_FORGE_PROBE=42\\n' > answer.txt; cat answer.txt"
        )
        if execution.get("exitCode") != 0 or "PI_FORGE_PROBE=42" not in execution["output"]:
            raise AssertionError("Native shell execution failed")
        result["native_rpc"] = True
        if not args.model:
            messages = (await runtime.command("get_messages"))["messages"]
            result.update(
                message_count=len(messages),
                pending="Pass --model provider/model with working PI credentials for "
                "agent and restart acceptance. PI defers disk persistence until "
                "an assistant message exists.",
            )
            return result
        if args.model:
            start = len(frames)
            await runtime.command(
                "prompt", message="Use a tool to read answer.txt, then report its exact contents."
            )
            while not any(e.get("type") == "agent_end" for e in frames[start:]):
                await runtime.read()
            assert_agent_turn(frames[start:])
            result["agent_turn"] = True
        state = await runtime.command("get_state")
        session_file = state.get("sessionFile")
        if not session_file or not Path(session_file).is_file():
            raise AssertionError("PI did not persist a session file")
        messages = (await runtime.command("get_messages"))["messages"]
        await runtime.stop()
        runtime = PiProbe(args, frames)
        await runtime.start(session_file)
        resumed = await runtime.command("get_state")
        replay = (await runtime.command("get_messages"))["messages"]
        if resumed.get("sessionId") != before.get("sessionId") or replay != messages:
            raise AssertionError("Native restart did not preserve session identity and messages")
        result.update(
            restart_replay=True,
            session_id=resumed["sessionId"],
            session_file=session_file,
            message_count=len(replay),
        )
        return result
    finally:
        await runtime.stop()
        (root / "frames.json").write_text(json.dumps(frames, indent=2) + "\n")
        (root / "result.json").write_text(json.dumps(result, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pi-bin", default="pi")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default="")
    parser.add_argument(
        "--isolated",
        action="store_true",
        help="Use a fresh PI configuration without the user's stored credentials",
    )
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--max-frame-bytes", type=int, default=8 * 1024 * 1024)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(probe(args)), indent=2))


if __name__ == "__main__":
    main()
