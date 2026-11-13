"""A mount's write policy: read-only, or confined to prefixes, held by the composite."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
import typer

from mimir.__main__ import _with_write_policy
from mimir.adapters.composite import CompositeMimirAdapter
from mimir.domain import MimirMount, WriteRouting
from mimir.exceptions import MimirWriteRefusedError
from mimir.mcp import MimirMcpServer
from niuu.domain.mimir import MimirSource, compute_content_hash
from tests.test_mimir.unit.fakes import InMemoryMimirAdapter

PAGE = "---\ntitle: T\nsummary: s\ncategory: project\n---\n\nbody\n"


def _port() -> MagicMock:
    port = MagicMock()
    for name in ("upsert_page", "delete_page", "ingest"):
        setattr(port, name, AsyncMock(return_value=[] if name == "ingest" else None))
    return port


def _composite(port, **policy) -> CompositeMimirAdapter:
    return CompositeMimirAdapter(
        mounts=[MimirMount(name="notes", port=port, role="local", **policy)],
        write_routing=WriteRouting(default=["notes"]),
    )


def _source() -> MimirSource:
    from datetime import UTC, datetime

    return MimirSource(
        source_id="src_1",
        title="t",
        content="c",
        source_type="document",
        origin_url=None,
        content_hash=compute_content_hash("c"),
        ingested_at=datetime.now(UTC),
    )


async def test_a_read_only_mount_refuses_every_write_and_counts_them():
    port = _port()
    composite = _composite(port, read_only=True)

    with pytest.raises(MimirWriteRefusedError, match="'notes'.*read-only"):
        await composite.upsert_page("project/a.md", PAGE)
    with pytest.raises(MimirWriteRefusedError):
        await composite.delete_page("project/a.md")
    with pytest.raises(MimirWriteRefusedError):
        await composite.ingest(_source())
    with pytest.raises(MimirWriteRefusedError):
        await composite.ingest_to(_source(), "notes")

    assert composite.write_refusals == {"notes": 4}
    for name in ("upsert_page", "delete_page", "ingest"):
        getattr(port, name).assert_not_awaited()


async def test_reads_still_work_on_a_read_only_mount():
    port = _port()
    port.list_pages = AsyncMock(return_value=[])
    composite = _composite(port, read_only=True)

    assert await composite.list_pages() == []
    assert composite.write_refusals == {}


async def test_a_prefix_mount_writes_inside_its_prefixes_and_refuses_outside():
    port = _port()
    composite = _composite(port, write_prefixes=("project/", "entity/"))

    await composite.upsert_page("project/a.md", PAGE)
    await composite.delete_page("entity/b.md")
    port.upsert_page.assert_awaited_once()

    with pytest.raises(MimirWriteRefusedError, match="'technical/a.md' is outside.*project/"):
        await composite.upsert_page("technical/a.md", PAGE)
    with pytest.raises(MimirWriteRefusedError, match="outside"):
        await composite.delete_page("other/b.md")
    assert composite.write_refusals == {"notes": 2}


async def test_a_write_that_cannot_be_confined_to_prefixes_is_refused():
    composite = _composite(_port(), write_prefixes=("project/",))
    with pytest.raises(MimirWriteRefusedError, match="cannot be confined"):
        await composite.ingest(_source())


async def test_a_mount_without_a_policy_writes_as_before():
    port = _port()
    composite = _composite(port)
    await composite.upsert_page("technical/a.md", PAGE)
    await composite.ingest(_source())
    port.upsert_page.assert_awaited_once()
    assert composite.write_refusals == {}


# --- the tool answers the model --------------------------------------------------------------


async def _call(server: MimirMcpServer, name: str, arguments: dict) -> dict:
    response = await server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )
    return response["result"]


async def test_the_mcp_tool_returns_the_refusal_as_a_tool_error():
    inner = InMemoryMimirAdapter()
    adapter = _with_write_policy(inner, "notes", True, [])
    server = MimirMcpServer(adapter=adapter, name="notes")

    refused = await _call(server, "mimir_write", {"path": "project/a.md", "content": "x"})

    assert refused["isError"] is True
    assert "Write refused" in refused["content"][0]["text"]
    assert inner.pages == {}
    assert adapter.write_refusals == {"notes": 1}


async def test_the_mcp_tool_writes_inside_the_prefixes_only():
    adapter = _with_write_policy(InMemoryMimirAdapter(), "notes", False, ["project/"])
    server = MimirMcpServer(adapter=adapter, name="notes")

    ok = await _call(server, "mimir_write", {"path": "project/a.md", "content": "x"})
    assert "isError" not in ok and json.loads(ok["content"][0]["text"])["path"] == "project/a.md"
    outside = await _call(server, "mimir_write", {"path": "technical/a.md", "content": "x"})
    assert outside["isError"] is True and "outside" in outside["content"][0]["text"]


# --- the flags --------------------------------------------------------------------------------


def test_the_flags_build_the_policy_and_no_flag_leaves_the_adapter_alone():
    inner = InMemoryMimirAdapter()
    assert _with_write_policy(inner, "n", False, []) is inner

    read_only = _with_write_policy(inner, "n", True, [])
    mount = read_only.mount("n")
    assert mount.read_only and mount.write_prefixes is None and mount.port is inner

    prefixed = _with_write_policy(inner, "n", False, ["a/", "b/"]).mount("n")
    assert not prefixed.read_only and prefixed.write_prefixes == ("a/", "b/")

    with pytest.raises(typer.BadParameter, match="exclude each other"):
        _with_write_policy(inner, "n", True, ["a/"])
