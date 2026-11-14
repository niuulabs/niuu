"""Local login runner tests: the real worker process, with an explicitly fake provider CLI."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from volundr.adapters.outbound import local_login_runner as llr
from volundr.adapters.outbound.local_login_runner import LocalLoginRunner
from volundr.adapters.outbound.login_worker import until_terminated
from volundr.domain.models import CredentialEnrollment, CredentialEnrollmentState

FAKE_CLAUDE = (
    f"#!{sys.executable}\n"
    "import os,sys,tty\n"
    "assert 'ANTHROPIC_API_KEY' not in os.environ\n"
    "tty.setraw(sys.stdin.fileno())\n"
    "print('https://claude.ai/oauth/authorize?state=test-only',flush=True)\n"
    "print('Paste code here if prompted >',flush=True)\n"
    "typed=b''\n"
    "while not typed.endswith(b'\\r'):\n"
    "    typed+=os.read(sys.stdin.fileno(),4096)\n"
    "assert typed==b'test-browser-code\\r'\n"
    "print('Your OAuth token (valid for 1 year):\\n'\n"
    "      'sk-ant-oat01-test-only-secret\\nStore this token securely.',flush=True)\n"
)

#: Waits for a code that never comes, recording its pid, so stopping it can be checked.
STUCK_CLAUDE = (
    f"#!{sys.executable}\n"
    "import os,sys,time\n"
    "open(os.environ['HOME']+'/../cli.pid','w').write(str(os.getpid()))\n"
    "print('https://claude.ai/oauth/authorize?state=test-only',flush=True)\n"
    "print('Paste code here if prompted >',flush=True)\n"
    "time.sleep(60)\n"
)


def enrollment(method: str = "claude_setup") -> CredentialEnrollment:
    now = datetime.now(UTC)
    return CredentialEnrollment(
        id=uuid4(),
        connection_id=str(uuid4()),
        owner_id="user",
        tenant_id="tenant",
        provider_slug="claude-code",
        credential_name="claude-code-credentials",
        method=method,
        state=CredentialEnrollmentState.PENDING,
        runner_ref={},
        verification_uri="",
        user_code="",
        expires_at=now + timedelta(minutes=15),
        error_code="",
        created_at=now,
        updated_at=now,
    )


def cli(tmp_path: Path, source: str, name: str = "claude") -> Path:
    path = tmp_path / "bin" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(source)
    path.chmod(0o700)
    return path


def runner(tmp_path: Path, claude: Path, **kwargs) -> LocalLoginRunner:
    logins = tmp_path / "logins"
    logins.mkdir(exist_ok=True)
    return LocalLoginRunner(
        claude_executable=str(claude), temporary_dir=str(logins), worker_interval=0.01, **kwargs
    )


async def poll_until(run: LocalLoginRunner, item: CredentialEnrollment, state: str):
    async with asyncio.timeout(10):
        while True:
            poll = await run.poll_enrollment(item)
            if poll.state.value == state:
                return poll
            if poll.state == CredentialEnrollmentState.FAILED:
                raise AssertionError(f"login failed: {poll.error_code}")
            await asyncio.sleep(0.02)


async def test_a_claude_login_on_this_host_signs_in_and_leaves_nothing_behind(tmp_path):
    run = runner(tmp_path, cli(tmp_path, FAKE_CLAUDE))
    item = await run.start_enrollment(enrollment())
    root = Path(item.runner_ref["root"])
    assert root.stat().st_mode & 0o777 == 0o700
    try:
        waiting = await poll_until(run, item, "awaiting_user")
        assert waiting.verification_uri == "https://claude.ai/oauth/authorize?state=test-only"

        await run.submit_code(item, "test-browser-code")
        done = await poll_until(run, item, "complete")
    finally:
        await run.cancel_enrollment(item)

    assert done.credential_data["token"] == "sk-ant-oat01-test-only-secret"
    assert not root.exists()


async def test_cancelling_a_login_stops_the_cli_too(tmp_path):
    run = runner(tmp_path, cli(tmp_path, STUCK_CLAUDE))
    item = await run.start_enrollment(enrollment())
    root = Path(item.runner_ref["root"])
    await poll_until(run, item, "awaiting_user")
    pid = int((root / "cli.pid").read_text())

    await run.cancel_enrollment(item)

    async with asyncio.timeout(5):
        while _alive(pid):
            await asyncio.sleep(0.02)
    assert not root.exists()


async def test_a_worker_that_died_is_a_failed_login_with_its_output_logged(tmp_path, caplog):
    run = runner(tmp_path, cli(tmp_path, STUCK_CLAUDE), worker_python=str(_crashing(tmp_path)))
    item = await run.start_enrollment(enrollment())
    try:
        async with asyncio.timeout(10):
            poll = await run.poll_enrollment(item)
            while poll.state == CredentialEnrollmentState.PENDING:
                await asyncio.sleep(0.02)
                poll = await run.poll_enrollment(item)
    finally:
        await run.cancel_enrollment(item)

    assert poll.state == CredentialEnrollmentState.FAILED
    assert poll.error_code == "login_worker_failed"
    assert "worker crashed" in caplog.text


async def test_a_login_started_before_a_restart_is_failed_and_its_directory_removed(tmp_path):
    item = enrollment()
    leftover = tmp_path / "leftover"
    leftover.mkdir()
    item = item.__class__(**{**item.__dict__, "runner_ref": {"root": str(leftover)}})
    run = runner(tmp_path, cli(tmp_path, FAKE_CLAUDE))

    poll = await run.poll_enrollment(item)
    await run.cancel_enrollment(item)

    assert poll.state == CredentialEnrollmentState.FAILED
    assert poll.error_code == "login_worker_missing"
    assert not leftover.exists()


class TestWhatItOffers:
    def test_a_cli_that_is_not_installed_is_not_offered(self, tmp_path, monkeypatch):
        monkeypatch.setattr(llr.shutil, "which", lambda name: None)
        run = LocalLoginRunner()

        assert not run.supports_enrollment("claude_setup")
        assert not run.supports_enrollment("codex_device")

    def test_an_installed_cli_is_found_on_path(self, tmp_path, monkeypatch):
        claude = cli(tmp_path, FAKE_CLAUDE)
        monkeypatch.setattr(
            llr.shutil, "which", lambda name: str(claude) if name == "claude" else None
        )
        run = LocalLoginRunner()

        assert run.supports_enrollment("claude_setup")
        assert not run.supports_enrollment("grok_device")

    def test_a_configured_executable_that_does_not_exist_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="does not exist"):
            LocalLoginRunner(claude_executable=str(tmp_path / "nope"))

    def test_a_configured_command_that_is_not_installed_is_not_offered(self, monkeypatch):
        monkeypatch.setattr(llr.shutil, "which", lambda name: None)

        assert not LocalLoginRunner(claude_executable="claude").supports_enrollment("claude_setup")

    async def test_starting_a_login_whose_cli_is_missing_names_it(self, tmp_path, monkeypatch):
        monkeypatch.setattr(llr.shutil, "which", lambda name: None)
        with pytest.raises(ValueError, match="needs the codex CLI"):
            await LocalLoginRunner().start_enrollment(enrollment("codex_device"))


class TestCodes:
    async def test_only_the_claude_login_takes_a_code(self, tmp_path):
        run = runner(tmp_path, cli(tmp_path, FAKE_CLAUDE))
        with pytest.raises(ValueError, match="does not accept"):
            await run.submit_code(enrollment("codex_device"), "x")

    async def test_a_code_for_a_login_that_is_not_running_is_refused(self, tmp_path):
        run = runner(tmp_path, cli(tmp_path, FAKE_CLAUDE))
        with pytest.raises(ValueError, match="not running"):
            await run.submit_code(enrollment(), "x")


async def test_a_worker_that_ignores_sigterm_is_killed():
    process = Mock()
    process.returncode = None
    process.pid = 1
    process.wait = AsyncMock(side_effect=[TimeoutError(), None])

    await llr._stop(process, 0.01)

    process.terminate.assert_called_once()
    process.kill.assert_called_once()


async def test_sigterm_cancels_the_login_so_it_stops_its_cli():
    stopped = asyncio.Event()

    async def login() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            stopped.set()
            raise

    task = asyncio.create_task(until_terminated(login()))
    await asyncio.sleep(0)
    os.kill(os.getpid(), signal.SIGTERM)
    async with asyncio.timeout(5):
        await task
    assert stopped.is_set()


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _crashing(tmp_path: Path) -> Path:
    path = tmp_path / "bin" / "python-crash"
    path.write_text("#!/bin/sh\necho 'worker crashed' >&2\nexit 3\n")
    path.chmod(0o700)
    return path


def test_the_cli_directory_is_searched_first(tmp_path):
    assert llr._search_path("/opt/homebrew/bin/codex").split(os.pathsep)[0] == "/opt/homebrew/bin"


@pytest.fixture(autouse=True)
def _restore_sigterm():
    previous = signal.getsignal(signal.SIGTERM)
    yield
    signal.signal(signal.SIGTERM, previous)
