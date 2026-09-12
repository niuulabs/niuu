"""Exercise the real subprocess boundary using an explicitly fake Codex CLI."""

import asyncio
import json
import sys
from unittest.mock import AsyncMock, patch

import pytest

from volundr.adapters.outbound.login_worker import (
    ANSI_ESCAPE,
    claude_login,
    codex_login,
    grok_login,
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
        "import os,sys,tty\n"
        "assert sys.stdin.isatty() and not sys.stdout.isatty()\n"
        "tty.setraw(sys.stdin.fileno())\n"
        "assert 'ANTHROPIC_API_KEY' not in os.environ\n"
        "print('https://claude.ai/oauth/authorize?state=test-only',flush=True)\n"
        "print('Paste code here if prompted >',flush=True)\n"
        "assert os.read(sys.stdin.fileno(),4096)==b'test-browser-code'\n"
        "assert os.read(sys.stdin.fileno(),4096)==b'\\r'\n"
        "print('Your OAuth token (valid for 1 year):\\n'\n"
        "      'sk-ant-oat01-test-only-\\nsecret\\nStore this token securely.',flush=True)\n"
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


@pytest.mark.parametrize("success", [True, False])
async def test_grok_device_login_captures_the_session_file(tmp_path, success):
    """The fake CLI prints the real challenge shape and writes $GROK_HOME/auth.json."""
    executable = tmp_path / "fake-grok"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json,os,sys\n"
        "assert sys.argv[1:]==['login','--device-auth']\n"
        "assert 'XAI_API_KEY' not in os.environ\n"
        "home=os.environ['GROK_HOME']\n"
        "print('\\nTo sign in, open this URL in your browser:\\n\\n'\n"
        "      '  https://accounts.x.ai/oauth2/device?user_code=5X2D-56W6\\n\\n'\n"
        "      'Confirm this code in your browser:\\n\\n  5X2D-56W6\\n\\n'\n"
        "      'Waiting for authorization...',flush=True)\n"
        f"ok={success!r}\n"
        "if ok:\n"
        "    open(os.path.join(home,'auth.json'),'w').write(json.dumps({'token':'grok-secret'}))\n"
        "    sys.exit(0)\n"
        "sys.exit(3)\n"
    )
    executable.chmod(0o700)
    task = asyncio.create_task(grok_login(tmp_path, str(executable)))
    async with asyncio.timeout(5):
        if success:
            await task
        else:
            with pytest.raises(RuntimeError, match="grok_exit_3"):
                await task
    status = json.loads((tmp_path / "status.json").read_text())
    if success:
        assert status == {"state": "complete"}
        credential = json.loads((tmp_path / "credential.json").read_text())
        assert json.loads(credential["auth.json"]) == {"token": "grok-secret"}
    else:
        assert status["state"] == "awaiting_user"
        assert status["verification_uri"] == (
            "https://accounts.x.ai/oauth2/device?user_code=5X2D-56W6"
        )
        assert status["user_code"] == "5X2D-56W6"
        assert not (tmp_path / "credential.json").exists()


async def test_grok_login_without_session_file_is_a_visible_failure(tmp_path):
    executable = tmp_path / "fake-grok"
    executable.write_text(f"#!{sys.executable}\nprint('done',flush=True)\n")
    executable.chmod(0o700)
    with pytest.raises(RuntimeError, match="credential_missing"):
        await grok_login(tmp_path, str(executable))


@pytest.mark.parametrize("method", ["codex_device", "claude_setup", "grok_device", "unknown"])
async def test_worker_deadline_and_safe_error_output(tmp_path, method):
    with (
        patch("os.umask"),
        patch("volundr.adapters.outbound.login_worker.codex_login", new_callable=AsyncMock),
        patch("volundr.adapters.outbound.login_worker.claude_login", new_callable=AsyncMock),
        patch("volundr.adapters.outbound.login_worker.grok_login", new_callable=AsyncMock),
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


def test_terminal_hyperlinks_do_not_erase_text_between_them():
    output = (
        "\x1b]8;;https://example.com\x1b\\login\x1b]8;;\x1b\\\n"
        "sk-ant-oat01-test-only-secret\n"
        "\x1b]8;;https://example.com\x1b\\docs\x1b]8;;\x1b\\"
    )
    assert ANSI_ESCAPE.sub("", output) == "login\nsk-ant-oat01-test-only-secret\ndocs"


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("claude_exit_-9", "claude_exit_-9"),
        ("claude_token_not_found", "claude_token_not_found"),
        ("unexpected secret from provider", "provider_login_failed"),
    ],
)
async def test_worker_preserves_only_safe_diagnostic_codes(tmp_path, reason, expected):
    with (
        patch("os.umask"),
        patch(
            "volundr.adapters.outbound.login_worker.claude_login",
            new_callable=AsyncMock,
            side_effect=RuntimeError(reason),
        ),
    ):
        await run(tmp_path, "claude_setup", "test-only-cli", 0.01)
    assert json.loads((tmp_path / "status.json").read_text())["error_code"] == expected
