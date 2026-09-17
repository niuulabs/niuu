"""END-TO-END Grok: the real `grok` CLI, driven through GrokACPTransport.

Everything else in the Grok suite is a unit test against captured frames. These
drive the ACTUAL binary, because the two defects that broke Grok in production
were both invisible to mocks:

  * the configured model id (`grok-build`) was not a real model, and the CLI
    rejects an unknown id **while exiting 0** — so a mocked spawn looked perfect
    and every real session died on its first prompt;
  * the agent-level flags have to precede the `stdio` subcommand, which only the
    real clap parser enforces.

A mock can only ever assert what we already believe. These assert what Grok does.

Requirements (all skipped cleanly when absent, never failed):
  * `grok` on PATH,
  * a logged-in CLI (`grok models` succeeds — OIDC via ~/.grok/auth.json),
  * network, and real tokens: these cost money and take ~1-2 minutes.

Run them explicitly:

    pytest tests/test_skuld/test_grok_e2e.py -m e2e -v
"""

import asyncio
import os
import shutil
import subprocess

import pytest

from skuld.transports import GrokACPTransport
from skuld.transports.grok import GROK_DEFAULT_MODEL

pytestmark = [pytest.mark.e2e, pytest.mark.live_cli, pytest.mark.usefixtures("grok_preflight")]


def _grok_available() -> tuple[bool, str]:
    """(usable, why-not). Auth is checked via `grok models`, which needs a live login."""
    if not shutil.which("grok"):
        return False, "grok CLI not on PATH"
    try:
        out = subprocess.run(["grok", "models"], capture_output=True, text=True, timeout=90)
    except Exception as exc:  # pragma: no cover - environment dependent
        return False, f"`grok models` failed: {exc!r}"
    blob = (out.stdout or "") + (out.stderr or "")
    if "not authenticated" in blob.lower():
        return False, "grok CLI is not logged in (run `grok login`)"
    if "Available models" not in blob:
        return False, f"unexpected `grok models` output: {blob[:200]!r}"
    return True, blob


@pytest.fixture(scope="module")
def grok_preflight():
    # Never start CLIs or contact a provider during test collection.
    if os.environ.get("FORGE_LIVE_CLI") != "1":
        pytest.skip("set FORGE_LIVE_CLI=1 to opt into real provider tests")
    usable, detail = _grok_available()
    if not usable:
        pytest.fail(f"Grok live gate prerequisite failed: {detail}")
    return detail


def test_the_configured_default_model_actually_exists(grok_preflight):
    """The regression that took Grok down, asserted against the live catalogue.

    `grok models` is the only authority on model ids. Our default must appear in
    it — a wrong id fails at session start with `unknown model id` and, because
    the CLI still exits 0, reads as a clean run all the way up the stack.
    """
    assert GROK_DEFAULT_MODEL in grok_preflight, (
        f"default model {GROK_DEFAULT_MODEL!r} is not in `grok models`:\n{grok_preflight}"
    )


def test_an_unknown_model_id_is_rejected_with_a_nonzero_exit():
    """An unknown model id fails loudly: clear message AND a non-zero exit.

    Worth pinning because the id is otherwise only validated at session start,
    deep inside the ACP handshake, where the failure is far less legible.
    """
    out = subprocess.run(
        ["grok", "-p", "hi", "-m", "grok-build", "--output-format", "json"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    blob = (out.stdout or "") + (out.stderr or "")
    assert "unknown model id" in blob.lower()
    assert out.returncode != 0, "an unknown model must fail the process, not pass silently"


def test_real_turn_yields_paired_tool_calls_with_ids_and_timing(tmp_path):
    """One real tool-using turn, asserted the way hierarchical mode consumes it.

    This is the shape the iOS transcript needs: every tool_use carries an id,
    every completed call produces exactly one tool_result carrying that id, and
    the result is stamped so a row can say how long the tool took.
    """
    from niuu.domain.transcript_reducer import TOOL_ENDED_AT

    events: list[dict] = []

    async def run() -> None:
        transport = GrokACPTransport(str(tmp_path), model=GROK_DEFAULT_MODEL)
        transport.on_event(lambda ev: events.append(ev))
        await transport.start()
        try:
            await transport.send_message(
                "Write a file called e2e.txt containing exactly the word ready. "
                "Then read it back to confirm. Use your tools."
            )
        finally:
            await transport.stop()

    asyncio.run(run())

    assert events, "no events streamed from a real Grok turn"

    def blocks(kind: str) -> list[dict]:
        found = []
        for ev in events:
            content = ev.get("content") or (ev.get("message") or {}).get("content") or []
            if isinstance(content, list):
                found += [b for b in content if isinstance(b, dict) and b.get("type") == kind]
        return found

    tool_uses = blocks("tool_use")
    tool_results = blocks("tool_result")

    assert tool_uses, "the prompt asks for file tools; Grok produced no tool_use at all"

    # 1. Every call is correlatable. Without ids the transcript cannot pair anything.
    unidentified = [b for b in tool_uses if not b.get("id")]
    assert not unidentified, f"{len(unidentified)} tool_use blocks carry no id: {unidentified[:2]}"

    # 2. Ids are unique — a duplicate means progress updates leaked back in as calls.
    ids = [b["id"] for b in tool_uses]
    assert len(ids) == len(set(ids)), f"duplicate tool_use ids (progress re-emitted?): {ids}"

    # 3. Results pair to real calls, and no call is reported twice.
    assert tool_results, "no tool_result — output would never attach to its call"
    result_ids = [b.get("tool_use_id") for b in tool_results]
    assert len(result_ids) == len(set(result_ids)), f"duplicate tool_results: {result_ids}"
    assert set(result_ids) <= set(ids), (
        f"tool_result(s) reference unknown calls: {set(result_ids) - set(ids)}"
    )

    # 4. Timing is present, so a hierarchical row can render a duration.
    stamped = [b for b in tool_results if b.get(TOOL_ENDED_AT)]
    assert stamped, "no per-tool end stamp — rows cannot show how long a tool took"

    # 5. Names are normalized to the cross-engine spelling, not raw Grok/opencode ids.
    names = {b.get("name") for b in tool_uses}
    assert not (names & {"write", "read", "run_terminal_command", "todo_write"}), (
        f"un-normalized tool names leaked through: {names}"
    )

    # 6. The model actually answered.
    text = "".join(
        (ev.get("delta") or {}).get("text", "")
        for ev in events
        if ev.get("type") == "content_block_delta"
    )
    assert text.strip(), "no assistant text streamed"

    # 7. And it really did the work on disk — the end of "end to end".
    assert (tmp_path / "e2e.txt").exists(), "Grok reported tool use but wrote no file"
    assert "ready" in (tmp_path / "e2e.txt").read_text().lower()


def test_real_turn_streams_reasoning_separately_from_the_answer(tmp_path):
    """Thinking must land as thinking_delta, never inlined into the answer text.

    Grok streams far more thought than message (29 vs 16 chunks in the captured
    corpus), so leaking it into the answer would swamp the reply.
    """
    events: list[dict] = []

    async def run() -> None:
        transport = GrokACPTransport(str(tmp_path), model=GROK_DEFAULT_MODEL)
        transport.on_event(lambda ev: events.append(ev))
        await transport.start()
        try:
            await transport.send_message("What is 17 * 23? Think it through, then answer.")
        finally:
            await transport.stop()

    asyncio.run(run())

    deltas = [ev.get("delta") or {} for ev in events if ev.get("type") == "content_block_delta"]
    answer = "".join(d.get("text", "") for d in deltas if d.get("type") == "text_delta")
    thinking = "".join(d.get("thinking", "") for d in deltas if d.get("type") == "thinking_delta")

    assert answer.strip(), "no answer text"
    assert "391" in answer.replace(",", ""), f"expected 17*23=391 in the answer: {answer[:200]!r}"
    if thinking:
        assert "[thinking]" not in answer, "reasoning marker leaked into the answer stream"
