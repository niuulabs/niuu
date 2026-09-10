"""Exercise the real subprocess boundary using an explicitly fake Codex CLI."""

import asyncio
import json
import sys
from unittest.mock import AsyncMock, patch

import pytest

from volundr.adapters.outbound.login_worker import (
    claude_login,
    codex_login,
    run,
    stop_process,
    write_json,
)


@pytest.mark.parametrize("success", [True, False])
async def test_codex_login_protocol_and_secret_capture(tmp_path, monkeypatch, success):
    executable = tmp_path / "fake-codex"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json,os,sys\nfrom pathlib import Path\n"
        "assert 'OPENAI_API_KEY' not in os.environ\n"
        "def read(): return json.loads(sys.stdin.readline())\n"
        "def send(v): print(json.dumps(v),flush=True)\n"
        "assert read()['method']=='initialize'\n"
        "send({'id':1,'result':{}})\n"
        "assert read()['method']=='initialized'\n"
        "assert read()['params']=={'type':'chatgptDeviceCode'}\n"
        "send({'id':2,'result':{'type':'chatgptDeviceCode','loginId':'login',"
        "'verificationUrl':'https://auth.openai.com/codex/device','userCode':'TEST-CODE'}})\n"
        "auth={'tokens':{'access_token':'test-access','refresh_token':'test-refresh','id_token':'test-id'}}\n"
        "Path(os.environ['CODEX_HOME'],'auth.json').write_text(json.dumps(auth))\n"
        f"send({{'method':'account/login/completed','params':{{'loginId':'login','success':{success}}}}})\n"
        "sys.stdin.read()\n"
    )
    executable.chmod(0o700)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    if not success:
        with pytest.raises(RuntimeError, match="provider_login_rejected"):
            await codex_login(tmp_path, str(executable))
        assert not (tmp_path / "credential.json").exists()
        return
    await codex_login(tmp_path, str(executable))
    assert json.loads((tmp_path / "status.json").read_text()) == {"state": "complete"}
    secret = json.loads((tmp_path / "credential.json").read_text())
    assert json.loads(secret["auth.json"])["tokens"]["refresh_token"] == "test-refresh"
    assert "test-refresh" not in (tmp_path / "status.json").read_text()


async def test_claude_cli_authorization_code_is_consumed_and_token_is_kept_private(tmp_path):
    executable = tmp_path / "fake-claude"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import os,sys\n"
        "assert 'ANTHROPIC_API_KEY' not in os.environ\n"
        "print('https://claude.ai/oauth/authorize?state=test-only',flush=True)\n"
        "print('Paste code here if prompted >',flush=True)\n"
        "assert input()=='test-browser-code'\n"
        "print('Your token: sk-ant-oat01-test-only-secret',flush=True)\n"
    )
    executable.chmod(0o700)
    task = asyncio.create_task(claude_login(tmp_path, str(executable), 0.001))
    try:
        async with asyncio.timeout(5):
            while not (tmp_path / "status.json").exists():
                if task.done():
                    await task
                await asyncio.sleep(0.001)
            status = json.loads((tmp_path / "status.json").read_text())
            assert status["state"] == "awaiting_user"
            write_json(tmp_path / "code.json", {"code": "test-browser-code"})
            await task
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    assert not (tmp_path / "code.json").exists()
    credential = json.loads((tmp_path / "credential.json").read_text())
    assert credential["token"] == "sk-ant-oat01-test-only-secret"
    assert credential["expires_at"]
    assert "secret" not in (tmp_path / "status.json").read_text()


@pytest.mark.parametrize("method", ["codex_device", "claude_setup", "unknown"])
async def test_worker_deadline_and_safe_error_output(tmp_path, method):
    with (
        patch("os.umask"),
        patch("volundr.adapters.outbound.login_worker.codex_login", new_callable=AsyncMock),
        patch("volundr.adapters.outbound.login_worker.claude_login", new_callable=AsyncMock),
    ):
        await run(tmp_path, method, "test-only-cli", 0.01)
    status = json.loads((tmp_path / "status.json").read_text())
    assert status["state"] == ("failed" if method == "unknown" else "expired")
    assert "test-only-cli" not in (tmp_path / "status.json").read_text()


async def test_unresponsive_cli_is_killed_after_shutdown_deadline():
    process = AsyncMock()
    process.returncode = None
    from unittest.mock import Mock

    process.terminate = Mock()
    process.kill = Mock()
    # Use the actual timeout around a blocked process wait.
    process.wait = AsyncMock(side_effect=[TimeoutError(), None])
    await stop_process(process, 0.01)
    process.kill.assert_called_once()
