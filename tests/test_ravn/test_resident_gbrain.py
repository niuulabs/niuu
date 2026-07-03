"""Coverage for the GBrain resident-state adapter (default memory) — network faked."""

from __future__ import annotations

import pytest

from ravn.adapters.resident_state import gbrain as gbrain_mod
from ravn.adapters.resident_state.gbrain import (
    GBrainResidentStateAdapter,
    _entries_from_gbrain_search,
    _entries_from_gbrain_text_search,
    _gbrain_markdown,
    _gbrain_slug_segment,
    _mcp_text_result,
    _normalize_write_mode,
    _parse_mcp_http_response,
    _slug_from_ref,
)
from ravn.domain.models import TokenUsage
from ravn.domain.resident_continuation import (
    ResidentPolicyObservation,
    ResidentTurnRecord,
)

# --- pure helpers -------------------------------------------------------------


def test_entries_from_gbrain_search_json_and_text() -> None:
    e = _entries_from_gbrain_search('{"results": [{"path": "p1", "content": "c1"}]}', limit=5)
    assert e[0].path == "gbrain:p1" and e[0].content == "c1"
    assert _entries_from_gbrain_search('[{"slug": "s1"}]', limit=5)[0].path == "gbrain:s1"
    # invalid JSON falls back to text-line parsing
    text = _entries_from_gbrain_search("usage: x\nfirst hit\nsecond hit", limit=1)
    assert len(text) == 1 and text[0].summary == "first hit"


def test_entries_from_gbrain_text_search_skips_headers_and_limits() -> None:
    out = _entries_from_gbrain_text_search("Options:\nkeyword search results\nreal one", limit=5)
    assert [x.summary for x in out] == ["real one"]


def test_parse_mcp_http_response_sse_and_plain_and_invalid() -> None:
    assert _parse_mcp_http_response('data: {"result": {"ok": 1}}')["result"] == {"ok": 1}
    assert _parse_mcp_http_response('{"result": {}}')["result"] == {}
    with pytest.raises(RuntimeError):
        _parse_mcp_http_response("[]")


def test_mcp_text_result_joins_text_blocks() -> None:
    assert _mcp_text_result({"content": [{"type": "text", "text": "a"}, {"type": "x"}]}) == "a"
    assert _mcp_text_result({"content": "nope"}) == ""


def test_normalize_write_mode() -> None:
    assert _normalize_write_mode("PUT-PAGE") == "put_page"
    assert _normalize_write_mode("auto") == "auto"
    with pytest.raises(ValueError, match="write_mode"):
        _normalize_write_mode("bogus")


def test_markdown_and_slug_helpers() -> None:
    md = _gbrain_markdown("resident/x.md", "Title", "body")
    assert "# Title" in md and "body" in md
    assert _slug_from_ref("resident/continuation/turns/x.md").startswith("resident/")
    assert _slug_from_ref("turns/x.md").startswith("resident/ravn/")
    assert _gbrain_slug_segment("123abc").startswith("t")


# --- adapter orchestration (network/CLI stubbed) ------------------------------


class _StubGBrain(GBrainResidentStateAdapter):
    def __init__(self, *a, **k) -> None:
        super().__init__(*a, **k)
        self.put_pages: list[tuple] = []
        self.ingests: list[tuple] = []

    async def _put_page_gbrain(self, ref, title, content) -> None:
        self.put_pages.append((ref, title, content))

    async def _ingest_gbrain(self, slug, content) -> None:
        self.ingests.append((slug, content))

    async def _call_mcp_tool(self, name, arguments):
        payload = '{"results": [{"path": "m1", "content": "c"}]}'
        return {"content": [{"type": "text", "text": payload}]}


class _Result:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _turn() -> ResidentTurnRecord:
    return ResidentTurnRecord(
        turn_index=1,
        prompt="p",
        response="r",
        outcome_fields={},
        tool_names=(),
        usage=TokenUsage(input_tokens=1, output_tokens=1),
    )


@pytest.mark.asyncio
async def test_put_page_capture_and_mcp_recall(tmp_path) -> None:
    adapter = _StubGBrain(tmp_path, write_mode="put_page", mcp_url="https://b", api_token="tok")
    await adapter.write_turn(_turn())
    assert adapter.put_pages  # projected via put_page
    recalled = await adapter.recall("topic", limit=5)
    assert recalled[0].path == "gbrain:m1"


@pytest.mark.asyncio
async def test_ingest_mode_projects_via_ingest(tmp_path) -> None:
    adapter = _StubGBrain(
        tmp_path, write_mode="ingest", ingest_url="https://ingest", api_token="tok"
    )
    await adapter.write_policy_observation(
        ResidentPolicyObservation(subject="s", observation="o", source="x")
    )
    assert adapter.ingests  # _capture_local_ref -> _capture_gbrain -> _ingest_gbrain


@pytest.mark.asyncio
async def test_cli_capture_and_cli_search(tmp_path, monkeypatch) -> None:
    calls: list[list[str]] = []

    async def _fake_run(argv, **kwargs):
        calls.append(argv)
        if "search" in argv:
            return _Result(stdout='{"results": [{"path": "cli1", "content": "c"}]}')
        return _Result()

    monkeypatch.setattr(gbrain_mod, "run_command", _fake_run)
    # no remote configured -> capture + search go through the CLI
    adapter = GBrainResidentStateAdapter(tmp_path, command="gbrain")
    await adapter.write_turn(_turn())
    recalled = await adapter.recall("topic", limit=5)

    assert any("capture" in c for c in calls)
    assert recalled[0].path == "gbrain:cli1"


@pytest.mark.asyncio
async def test_search_disabled_returns_local_only(tmp_path) -> None:
    adapter = GBrainResidentStateAdapter(tmp_path, search_enabled=False, command="gbrain")
    assert await adapter.recall("topic") == []


@pytest.mark.asyncio
async def test_operator_markers_project_to_gbrain(tmp_path) -> None:
    adapter = _StubGBrain(tmp_path, write_mode="put_page", mcp_url="https://b", api_token="tok")
    await adapter.write_operator_needed(question="ok?", reason="r", turn=_turn())
    await adapter.write_operator_answer("yes")
    assert len(adapter.put_pages) >= 2


@pytest.mark.asyncio
async def test_call_mcp_tool_over_http(tmp_path) -> None:
    import httpx
    import respx

    adapter = GBrainResidentStateAdapter(
        tmp_path, mcp_url="https://brain.example/mcp", api_token="tok"
    )
    with respx.mock:
        route = respx.post("https://brain.example/mcp")
        route.mock(return_value=httpx.Response(200, text='{"result": {"content": []}}'))
        result = await adapter._call_mcp_tool("search", {"query": "x"})
        assert result == {"content": []}

        route.mock(return_value=httpx.Response(200, text='{"error": {"message": "boom"}}'))
        with pytest.raises(RuntimeError, match="boom"):
            await adapter._call_mcp_tool("search", {"query": "x"})

        is_error = '{"result": {"isError": true, "content": [{"type": "text", "text": "bad"}]}}'
        route.mock(return_value=httpx.Response(200, text=is_error))
        with pytest.raises(RuntimeError):
            await adapter._call_mcp_tool("search", {"query": "x"})


@pytest.mark.asyncio
async def test_ingest_gbrain_over_http_and_guard(tmp_path) -> None:
    import httpx
    import respx

    ok = GBrainResidentStateAdapter(
        tmp_path, ingest_url="https://brain.example/ingest", api_token="tok"
    )
    with respx.mock:
        respx.post("https://brain.example/ingest").mock(return_value=httpx.Response(202))
        await ok._ingest_gbrain("slug", "# note")  # must not raise

    missing = GBrainResidentStateAdapter(tmp_path, ingest_url="", api_token="")
    with pytest.raises(RuntimeError, match="ingest"):
        await missing._ingest_gbrain("slug", "# note")
