"""Acceptance must distinguish RPC acknowledgment from actual agent execution."""

import argparse
import asyncio
import json
from unittest.mock import AsyncMock, Mock

import pytest

from scripts import forge_pi_probe
from scripts.forge_pi_probe import PiProbe, assert_agent_turn, probe


def completed_turn():
    return [
        {"type": "message_update"},
        {"type": "tool_execution_end", "toolCallId": "read-1"},
        {
            "type": "agent_end",
            "messages": [
                {
                    "role": "assistant",
                    "stopReason": "stop",
                    "content": [
                        {"type": "text", "text": "PI_FORGE_PROBE=42"},
                    ],
                },
            ],
        },
    ]


def test_agent_acceptance_requires_actual_execution():
    assert_agent_turn(completed_turn())
    with pytest.raises(AssertionError, match="No agent_end"):
        assert_agent_turn([{"type": "response", "command": "prompt", "success": True}])


@pytest.mark.parametrize("reason", ["error", "aborted"])
def test_failed_or_aborted_turn_never_passes(reason):
    events = completed_turn()
    events[-1]["messages"][0]["stopReason"] = reason
    with pytest.raises(AssertionError, match="agent failed"):
        assert_agent_turn(events)


@pytest.mark.parametrize(
    "missing,expected",
    [
        ("tool_execution_end", "did not execute a tool"),
        ("message_update", "did not stream"),
    ],
)
def test_partial_trace_never_passes(missing, expected):
    with pytest.raises(AssertionError, match=expected):
        assert_agent_turn([e for e in completed_turn() if e["type"] != missing])


def test_empty_completion_never_passes():
    with pytest.raises(AssertionError, match="No completed assistant"):
        assert_agent_turn([{"type": "agent_end", "messages": []}])


def runtime_with_frames(frames):
    reader = asyncio.StreamReader()
    reader.feed_data(
        b"".join(json.dumps(frame, ensure_ascii=False).encode() + b"\n" for frame in frames)
    )
    reader.feed_eof()
    runtime = PiProbe(argparse.Namespace(timeout=1), [])
    runtime.process = Mock(stdout=reader, stdin=Mock(drain=AsyncMock()))
    return runtime


@pytest.mark.asyncio
async def test_command_correlates_reply_and_preserves_interleaved_events():
    runtime = runtime_with_frames(
        [
            {"type": "response", "id": "other", "success": True},
            {"type": "message_update", "text": "line\u2028separator\u2029intact"},
            {"type": "response", "id": "probe-1", "success": True, "data": {"answer": 42}},
        ]
    )
    assert await runtime.command("get_state") == {"answer": 42}
    assert len(runtime.frames) == 3
    assert runtime.frames[1]["text"] == "line\u2028separator\u2029intact"


@pytest.mark.asyncio
async def test_rejected_command_is_not_success():
    runtime = runtime_with_frames(
        [
            {"type": "response", "id": "probe-1", "success": False, "error": "No model"},
        ]
    )
    with pytest.raises(RuntimeError, match="No model"):
        await runtime.command("prompt", message="work")


@pytest.mark.asyncio
async def test_eof_and_non_object_frames_fail():
    with pytest.raises(RuntimeError, match="closed stdout"):
        await runtime_with_frames([]).read()
    with pytest.raises(ValueError, match="non-object"):
        await runtime_with_frames([[]]).read()


def options(root, model=""):
    return argparse.Namespace(
        output_dir=root,
        model=model,
        pi_bin="/test/pi",
        isolated=True,
        timeout=1,
        max_frame_bytes=1024,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("resume,model", [(None, ""), ("saved.jsonl", "provider/model")])
async def test_native_launch_uses_owned_storage_and_explicit_options(
    tmp_path, monkeypatch, resume, model
):
    process = Mock(returncode=None, wait=AsyncMock())
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    runtime = PiProbe(options(tmp_path, model), [])
    await runtime.start(resume)
    command = spawn.call_args.args
    assert command[:3] == ("/test/pi", "--mode", "rpc")
    assert str(tmp_path / "sessions") in command
    assert spawn.call_args.kwargs["env"]["PI_CODING_AGENT_DIR"] == str(tmp_path / "isolated-agent")
    if resume:
        assert command[-4:] == ("--session", resume, "--model", model)
    await runtime.stop()
    process.terminate.assert_called_once()
    process.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_stop_kills_a_process_that_ignores_termination():
    runtime = PiProbe(argparse.Namespace(timeout=1), [])
    await runtime.stop()
    process = Mock(returncode=None, wait=AsyncMock(side_effect=[TimeoutError, None]))
    runtime.process = process
    await runtime.stop()
    process.kill.assert_called_once()
    process.returncode = 0
    await runtime.stop()
    assert process.terminate.call_count == 1


def fake_native_runtime(monkeypatch, *, bad_bash=False, persist=True, mismatch=False):
    """RPC fixtures stand in for a credentialed process, not for Forge acceptance."""
    instances = []

    class Runtime:
        def __init__(self, args, frames):
            self.args, self.frames = args, frames
            self.resumed = False
            self.stopped = False
            instances.append(self)

        async def start(self, session=None):
            self.resumed = session is not None

        async def stop(self):
            self.stopped = True

        async def command(self, kind, **kwargs):
            session = self.args.output_dir / "native.jsonl"
            if kind == "get_state":
                return {
                    "sessionId": "wrong" if mismatch and self.resumed else "pi-1",
                    "sessionFile": str(session),
                }
            if kind == "bash":
                return {"exitCode": 1 if bad_bash else 0, "output": "PI_FORGE_PROBE=42"}
            if kind == "get_messages":
                return {"messages": [{"role": "assistant", "content": "PI_FORGE_PROBE=42"}]}
            if kind == "prompt":
                if persist:
                    session.write_text("native fixture\n")
                self.frames.extend(completed_turn())
                return {}
            raise AssertionError(kind)

    monkeypatch.setattr(forge_pi_probe, "PiProbe", Runtime)
    return instances


@pytest.mark.asyncio
async def test_native_replay_acceptance_does_not_claim_forge_or_ios(tmp_path, monkeypatch):
    instances = fake_native_runtime(monkeypatch)
    result = await probe(options(tmp_path, "provider/model"))
    assert result["native_rpc"] and result["agent_turn"] and result["restart_replay"]
    assert result["forge_database_replay"] is False
    assert result["ios_replay"] is False
    assert len(instances) == 2 and all(i.stopped for i in instances)
    assert json.loads((tmp_path / "result.json").read_text()) == result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,expected",
    [
        ({"bad_bash": True}, "shell execution failed"),
        ({"persist": False}, "did not persist"),
        ({"mismatch": True}, "did not preserve"),
    ],
)
async def test_failed_native_acceptance_retains_evidence(tmp_path, monkeypatch, failure, expected):
    instances = fake_native_runtime(monkeypatch, **failure)
    with pytest.raises(AssertionError, match=expected):
        await probe(options(tmp_path, "provider/model"))
    assert all(i.stopped for i in instances)
    assert (tmp_path / "frames.json").is_file()
    assert json.loads((tmp_path / "result.json").read_text())["restart_replay"] is False


def test_cli_without_model_reports_only_native_smoke(tmp_path, monkeypatch, capsys):
    fake_native_runtime(monkeypatch)
    monkeypatch.setattr(
        "sys.argv", ["forge_pi_probe.py", "--output-dir", str(tmp_path), "--isolated"]
    )
    forge_pi_probe.main()
    result = json.loads(capsys.readouterr().out)
    assert result["native_rpc"] is True
    assert result["agent_turn"] is False and result["restart_replay"] is False
    assert "--model" in result["pending"]
