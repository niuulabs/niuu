"""END-TO-END Muse: the real `muse serve` host, driven through MuseMSPTransport.

Everything else in the Muse suite replays recorded frames. These drive the ACTUAL
binary, because the things that break a stdio protocol client are invisible to fakes:
argument order (`serve` must precede its flags), the handshake's exact shape
(`clientInfo.name` must match ^[a-z0-9_]+$), request/response correlation, and what
the host really does when a turn fails.

Requirements (skipped cleanly when absent, never failed):
  * `muse` on PATH (curl -fsSL https://dev.meta.ai/install.sh | bash),
  * a writable HOME (the host persists sessions under ~/.local/share/muse).

Without a credential (META_API_KEY / `muse auth set`) every turn ends `failed` with
"not logged in" — which is itself the behaviour asserted here. With one, the same
test asserts a completed turn instead (it costs a few tokens).

Run them explicitly:

    pytest tests/test_skuld/test_muse_e2e.py -m e2e -v
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess

import pytest

from skuld.transports import MuseMSPTransport

pytestmark = [pytest.mark.e2e, pytest.mark.live_cli, pytest.mark.usefixtures("muse_preflight")]


def _muse_available() -> tuple[bool, str]:
    if not shutil.which("muse"):
        return False, "muse CLI not on PATH"
    try:
        out = subprocess.run(["muse", "--version"], capture_output=True, text=True, timeout=60)
    except Exception as exc:  # pragma: no cover - environment dependent
        return False, f"`muse --version` failed: {exc!r}"
    blob = (out.stdout or "") + (out.stderr or "")
    if "Muse Code" not in blob:
        return False, f"unexpected `muse --version` output: {blob[:200]!r}"
    return True, blob.strip()


@pytest.fixture(scope="module")
def muse_preflight():
    # Never start CLIs or contact a provider during test collection.
    if os.environ.get("FORGE_LIVE_CLI") != "1":
        pytest.skip("set FORGE_LIVE_CLI=1 to opt into real provider tests")
    usable, detail = _muse_available()
    if not usable:
        pytest.fail(f"Muse live gate prerequisite failed: {detail}")
    return detail


_HAS_CREDENTIAL = bool(os.environ.get("META_API_KEY")) or os.path.exists(
    os.path.join(
        os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "muse", "auth.json"
    )
)


def _git_workspace(tmp_path) -> str:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "README.md").write_text("# e2e workspace\n")
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    subprocess.run(
        ["git", "-C", str(ws), "-c", "user.email=e2e@x", "-c", "user.name=e2e", "add", "-A"],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(ws),
            "-c",
            "user.email=e2e@x",
            "-c",
            "user.name=e2e",
            "commit",
            "-qm",
            "init",
        ],
        check=True,
    )
    return str(ws)


def test_serve_handshake_session_turn_and_resume(tmp_path):
    """One real host: handshake, session/start, a turn, then a second host resuming it."""
    ws = _git_workspace(tmp_path)

    async def run() -> None:
        events: list[dict] = []
        t = MuseMSPTransport(ws, acp_prompt_timeout_s=120.0)
        t.on_event(lambda ev: events.append(ev))
        await t.start()
        try:
            assert t.is_alive
            assert t.session_id, "session/start returned no session id"
            assert t._server_info.get("name") == "muse"
            await asyncio.wait_for(t.send_message("Reply with exactly the word: pong"), timeout=150)
        finally:
            await t.stop()

        results = [e for e in events if e.get("type") == "result"]
        assert len(results) == 1, f"expected one result, got {results}"
        result = results[0]
        assert result["sessionId"] == t.session_id
        assert result["modelUsage"], "a result must carry usage so message_count advances"
        if _HAS_CREDENTIAL:
            assert result["stop_reason"] == "end_turn", result
            assert (
                "pong"
                in "".join(
                    e["delta"]["text"]
                    for e in events
                    if e.get("type") == "content_block_delta" and "text" in e["delta"]
                ).lower()
            )
        else:
            # No credential: the host serves, the turn fails typed, and the transport
            # surfaces WHY instead of wedging.
            assert result["stop_reason"] == "error", result
            assert result["is_error"] is True
            assert "not logged in" in result["error"]
        assert not t.is_turn_active

        # Resume the same durable session on a fresh host — the id round-trips.
        first_session = t.session_id
        t2 = MuseMSPTransport(ws, resume_session_id=first_session)
        resumed: list[dict] = []
        t2.on_event(lambda ev: resumed.append(ev))
        await t2.start()
        try:
            assert t2.session_id == first_session
            assert t2.is_alive
            # Interrupting an idle session is a clean rejection, never a hang.
            await asyncio.wait_for(t2.send_control("interrupt"), timeout=30)
        finally:
            await t2.stop()

    asyncio.run(run())


def test_serve_rejects_flags_before_the_verb(tmp_path):
    """`muse --disable-sandbox serve` is parsed as TUI options and exits 2 — the transport
    must always put `serve` first (the regression the Grok transport hit with `stdio`)."""
    proc = subprocess.run(
        ["muse", "--disable-sandbox", "serve"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=_git_workspace(tmp_path),
        input="",
    )
    assert proc.returncode != 0
    assert "serve" in (proc.stderr or "")
    cmd = MuseMSPTransport(str(tmp_path))._serve_command("muse")
    assert cmd[1] == "serve"
