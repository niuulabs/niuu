"""Short-lived official CLI login worker. Secrets stay in its private temporary home.

Invoked by the enrollment runner, never as a model/session runtime. Nothing from
the provider process is copied to container logs.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import fcntl
import json
import os
import pty
import re
import signal
import struct
import termios
from collections.abc import Coroutine
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ANSI_ESCAPE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\](?:[^\x07\x1b]|\x1b(?!\\))*(?:\x07|\x1b\\)|[@-_])"
)
CLAUDE_TOKEN = re.compile(
    r"Your OAuth token[^\r\n]*:\s*(.*?)\s*Store this token securely", re.DOTALL
)
CLAUDE_TOKEN_LIFETIME_DAYS = 365  # Duration documented by `claude setup-token`.
# The grok CLI documents that `auth.json` tokens expire after 7 days and asks
# for a new `grok login`; the device flow issues no refresh token.
GROK_TOKEN_LIFETIME_DAYS = 7
DEFAULT_SHUTDOWN_TIMEOUT = 2.0
#: Where the CLIs are in the login image. A worker run on a host is given the host's.
CONTAINER_SEARCH_PATH = "/usr/local/bin:/usr/bin:/bin"


async def stop_process(process: asyncio.subprocess.Process, timeout: float) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout)
    except TimeoutError:
        process.kill()
        await process.wait()


async def claude_login(
    root: Path,
    executable: str,
    interval: float,
    shutdown_timeout: float = DEFAULT_SHUTDOWN_TIMEOUT,
    search_path: str = CONTAINER_SEARCH_PATH,
) -> None:
    master, slave = pty.openpty()
    output_reader, output_writer = os.pipe()
    # Only stdin is a terminal. Piped output makes the CLI emit complete frames
    # instead of screen deltas that cannot be decoded by stripping ANSI codes.
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 8192, 0, 0))
    environment = {
        "PATH": search_path,
        "HOME": str(root),
        "CLAUDE_CONFIG_DIR": str(root / "claude"),
        "BROWSER": "/usr/bin/true",
        "TERM": "xterm",
        "NO_COLOR": "1",
    }
    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            "setup-token",
            stdin=slave,
            stdout=output_writer,
            stderr=output_writer,
            env=environment,
            cwd=root,
            start_new_session=True,
        )
    finally:
        os.close(slave)
        os.close(output_writer)
    os.set_blocking(output_reader, False)
    output = ""
    challenge_sent = False
    try:
        while True:
            try:
                chunk = os.read(output_reader, 65536)
            except BlockingIOError:
                chunk = None
            except OSError:
                # PTYs report EIO when the child closes its end.
                chunk = b""
            if chunk:
                output += chunk.decode(errors="replace")
                clean = ANSI_ESCAPE.sub("", output)
                if not challenge_sent:
                    match = re.search(r"https://[^\s\x1b]+", clean)
                    if match and "Paste" in clean[match.end() :]:
                        uri = match.group(0)
                        parsed = urlparse(uri)
                        if (parsed.netloc, parsed.path) not in {
                            ("claude.ai", "/oauth/authorize"),
                            ("platform.claude.com", "/oauth/authorize"),
                            ("claude.com", "/cai/oauth/authorize"),
                        }:
                            raise RuntimeError("unexpected_login_url")
                        write_json(
                            root / "status.json",
                            {
                                "state": "awaiting_user",
                                "verification_uri": uri,
                                "user_code": "",
                            },
                        )
                        challenge_sent = True
            code_path = root / "code.json"
            if code_path.exists():
                code = json.loads(code_path.read_text())["code"]
                code_path.unlink()
                # Terminal UIs treat a multi-character read as pasted text. Send
                # Enter in a separate event so it submits rather than joins the paste.
                os.write(master, code.encode())
                await asyncio.sleep(interval)
                os.write(master, b"\r")
            if chunk == b"" or (process.returncode is not None and chunk is None):
                break
            await asyncio.sleep(interval)
        await process.wait()
        clean = ANSI_ESCAPE.sub("", output)
        token = CLAUDE_TOKEN.search(clean)
        if process.returncode != 0:
            raise RuntimeError(f"claude_exit_{process.returncode}")
        if token is None:
            raise RuntimeError("claude_token_not_found")
        expiry = datetime.now(UTC) + timedelta(days=CLAUDE_TOKEN_LIFETIME_DAYS)
        write_json(
            root / "credential.json",
            {"token": "".join(token.group(1).split()), "expires_at": expiry.isoformat()},
        )
        write_json(root / "status.json", {"state": "complete"})
    finally:
        await stop_process(process, shutdown_timeout)
        os.close(master)
        os.close(output_reader)


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value))
    temporary.replace(path)


async def codex_login(
    root: Path,
    executable: str,
    shutdown_timeout: float = DEFAULT_SHUTDOWN_TIMEOUT,
    search_path: str = os.defpath,
) -> None:
    home = root / "codex"
    home.mkdir(mode=0o700)
    (home / "config.toml").write_text('cli_auth_credentials_store = "file"\n')
    # Use an allowlist: never inherit the API service's credentials or an
    # operator's own CLI account into another user's login.
    environment = {"PATH": search_path, "HOME": str(root), "CODEX_HOME": str(home)}
    process = await asyncio.create_subprocess_exec(
        executable,
        "app-server",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=environment,
        cwd=root,
    )
    assert process.stdin is not None and process.stdout is not None

    async def send(message: dict) -> None:
        process.stdin.write((json.dumps(message) + "\n").encode())
        await process.stdin.drain()

    try:
        await send(
            {
                "id": 1,
                "method": "initialize",
                "params": {"clientInfo": {"name": "niuu_login", "version": "1.0"}},
            }
        )
        login_id = None
        async for line in process.stdout:
            message = json.loads(line)
            if "error" in message:
                raise RuntimeError("provider_login_rejected")
            if message.get("id") == 1:
                await send({"method": "initialized", "params": {}})
                await send(
                    {
                        "id": 2,
                        "method": "account/login/start",
                        "params": {"type": "chatgptDeviceCode"},
                    }
                )
            if message.get("id") == 2:
                result = message["result"]
                login_id = result["loginId"]
                if result.get("type") != "chatgptDeviceCode":
                    raise RuntimeError("unexpected_login_method")
                # Do not let a compromised/misconfigured worker send a user to
                # an arbitrary credential-collection site.
                if result["verificationUrl"] != "https://auth.openai.com/codex/device":
                    raise RuntimeError("unexpected_login_url")
                write_json(
                    root / "status.json",
                    {
                        "state": "awaiting_user",
                        "verification_uri": result["verificationUrl"],
                        "user_code": result["userCode"],
                    },
                )
            if message.get("method") != "account/login/completed":
                continue
            result = message["params"]
            if not login_id or result.get("loginId") != login_id:
                continue
            if not result.get("success"):
                raise RuntimeError("provider_login_rejected")
            auth = json.loads((home / "auth.json").read_text())
            tokens = auth.get("tokens", {})
            if not all(tokens.get(key) for key in ("access_token", "refresh_token", "id_token")):
                raise RuntimeError("credential_missing")
            write_json(root / "credential.json", {"auth.json": json.dumps(auth)})
            write_json(root / "status.json", {"state": "complete"})
            return
        raise RuntimeError("login_process_exited")
    finally:
        await stop_process(process, shutdown_timeout)


GROK_DEVICE_URL = re.compile(r"https://accounts\.x\.ai/oauth2/device\?user_code=([A-Z0-9-]+)")


async def grok_login(
    root: Path,
    executable: str,
    shutdown_timeout: float = DEFAULT_SHUTDOWN_TIMEOUT,
    search_path: str = os.defpath,
) -> None:
    """``grok login --device-auth``: prints a verification URL and code, then waits.

    The CLI writes its session to ``$GROK_HOME/auth.json`` once the user has
    approved; that file is the credential (mounted read-only at ``~/.grok/auth.json``
    in sessions).
    """
    home = root / "grok"
    home.mkdir(mode=0o700)
    environment = {
        "PATH": search_path,
        "HOME": str(root),
        "GROK_HOME": str(home),
        "NO_COLOR": "1",
        "TERM": "xterm",
    }
    master, slave = pty.openpty()
    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            "login",
            "--device-auth",
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=environment,
            cwd=root,
            start_new_session=True,
        )
    finally:
        os.close(slave)
    os.set_blocking(master, False)
    output = ""
    challenge_sent = False
    try:
        while True:
            try:
                chunk = os.read(master, 65536)
            except BlockingIOError:
                chunk = None
            except OSError:
                chunk = b""
            if chunk:
                output += chunk.decode(errors="replace")
                if not challenge_sent:
                    match = GROK_DEVICE_URL.search(ANSI_ESCAPE.sub("", output))
                    if match:
                        write_json(
                            root / "status.json",
                            {
                                "state": "awaiting_user",
                                "verification_uri": match.group(0),
                                "user_code": match.group(1),
                            },
                        )
                        challenge_sent = True
            if chunk == b"" or (process.returncode is not None and chunk is None):
                break
            await asyncio.sleep(0.1)
        await process.wait()
        if process.returncode != 0:
            raise RuntimeError(f"grok_exit_{process.returncode}")
        auth = home / "auth.json"
        if not auth.exists():
            raise RuntimeError("credential_missing")
        expiry = datetime.now(UTC) + timedelta(days=GROK_TOKEN_LIFETIME_DAYS)
        write_json(
            root / "credential.json",
            {"auth.json": auth.read_text(), "expires_at": expiry.isoformat()},
        )
        write_json(root / "status.json", {"state": "complete"})
    finally:
        await stop_process(process, shutdown_timeout)
        os.close(master)


async def run(
    root: Path,
    method: str,
    executable: str,
    ttl: float,
    interval: float = 0.1,
    shutdown_timeout: float = DEFAULT_SHUTDOWN_TIMEOUT,
    search_path: str = "",
) -> None:
    """Run one login. ``search_path`` is the CLI's ``PATH``; empty keeps each CLI's
    container default."""
    os.umask(0o077)
    path = {"search_path": search_path} if search_path else {}
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_json(root / "status.json", {"state": "pending"})
    try:
        async with asyncio.timeout(ttl):
            if method == "codex_device":
                await codex_login(root, executable, shutdown_timeout, **path)
            elif method == "claude_setup":
                await claude_login(root, executable, interval, shutdown_timeout, **path)
            elif method == "grok_device":
                await grok_login(root, executable, shutdown_timeout, **path)
            else:
                raise RuntimeError("unsupported_method")
            # The controller reads the secret over Kubernetes exec, persists it
            # to OpenBao, and deletes the Job. Never put it in pod logs.
            await asyncio.Event().wait()
    except TimeoutError:
        write_json(root / "status.json", {"state": "expired"})
    except Exception as exc:
        # Only fixed diagnostic codes may leave the private CLI process.
        reason = str(exc)
        safe_reason = (
            reason
            if re.fullmatch(
                r"claude_exit_-?\d+|grok_exit_-?\d+|claude_token_not_found|"
                r"credential_missing|unexpected_login_url",
                reason,
            )
            else "provider_login_failed"
        )
        write_json(root / "status.json", {"state": "failed", "error_code": safe_reason})
        # Keep the safe error readable until the Job's deadline, even if the
        # user has closed the browser. Kubernetes then reaps the Job and home.
        await asyncio.sleep(ttl)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--method", required=True)
    parser.add_argument("--executable", required=True)
    parser.add_argument("--ttl", type=float, required=True)
    parser.add_argument("--interval", type=float, default=0.1)
    parser.add_argument("--shutdown-timeout", type=float, default=DEFAULT_SHUTDOWN_TIMEOUT)
    parser.add_argument("--search-path", default="")
    args = parser.parse_args()
    asyncio.run(
        until_terminated(
            run(
                args.root,
                args.method,
                args.executable,
                args.ttl,
                args.interval,
                args.shutdown_timeout,
                args.search_path,
            )
        )
    )


async def until_terminated(login: Coroutine[Any, Any, None]) -> None:
    """Run *login*, cancelling it on SIGTERM so its CLI is stopped on the way out.

    Each CLI runs in a session of its own, so a worker that simply died would leave it
    running. A container or Job takes the CLI down with it; a host process does not.
    """
    task = asyncio.ensure_future(login)
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
    with contextlib.suppress(asyncio.CancelledError):
        await task


if __name__ == "__main__":
    main()
