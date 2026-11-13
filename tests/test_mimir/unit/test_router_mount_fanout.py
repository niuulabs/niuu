"""Aggregate Mímir reads across mounts: concurrent, single-pass, failures loud.

Measured on yggdrasil (2026-11-03): ``/mimir/graph`` cost the sum of eight
mounts' graphs and ``/mimir/pages`` listed every mount twice.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.adapters.identity import EnvoyHeaderAuthenticationAdapter
from mimir.adapters.composite import CompositeMimirAdapter
from mimir.domain import MimirMount, WriteRouting
from mimir.exceptions import MimirUnavailableError
from mimir.router import MimirRouter
from niuu.ports.mimir import MimirPort
from tests.test_mimir.unit.fakes import InMemoryMimirAdapter

_HEADERS = {
    "x-auth-user-id": "test-user",
    "x-auth-tenant": "default",
    "x-auth-roles": "volundr:admin",
}


class _Counting(InMemoryMimirAdapter):
    """An in-memory store that counts listings and how many graph reads overlap."""

    def __init__(self, tracker: dict[str, int]) -> None:
        super().__init__()
        self.list_calls = 0
        self._tracker = tracker

    async def list_pages(self, category=None, prefix=None):  # noqa: ANN001, ANN201
        self.list_calls += 1
        return await super().list_pages(category=category, prefix=prefix)

    async def get_graph(self):  # noqa: ANN201
        self._tracker["in_flight"] += 1
        self._tracker["peak"] = max(self._tracker["peak"], self._tracker["in_flight"])
        await asyncio.sleep(0.05)
        try:
            return await super().get_graph()
        finally:
            self._tracker["in_flight"] -= 1


class _BrokenListing(InMemoryMimirAdapter):
    async def list_pages(self, category=None, prefix=None):  # noqa: ANN001, ANN201
        raise RuntimeError("connection refused")


def _client(mounts: list[tuple[str, MimirPort]]) -> TestClient:
    adapter = CompositeMimirAdapter(
        mounts=[
            MimirMount(name=name, port=port, role="shared", read_priority=index)
            for index, (name, port) in enumerate(mounts)
        ],
        write_routing=WriteRouting(default=[mounts[0][0]]),
    )
    router = MimirRouter(
        adapter=adapter, name=mounts[0][0], role="shared", auth=EnvoyHeaderAuthenticationAdapter()
    )
    app = FastAPI()
    app.include_router(router.router, prefix="/mimir")
    return TestClient(app, headers=_HEADERS)


def _page(adapter: InMemoryMimirAdapter, path: str, title: str) -> None:
    asyncio.run(adapter.upsert_page(path, f"# {title}\n\nAbout {title}.\n"))


class TestPages:
    def test_an_aggregate_listing_asks_each_mount_once(self) -> None:
        tracker = {"in_flight": 0, "peak": 0}
        local = _Counting(tracker)
        shared = _Counting(tracker)
        _page(local, "notes/a.md", "A")
        _page(shared, "notes/a.md", "A")
        _page(shared, "notes/b.md", "B")

        pages = _client([("local", local), ("shared", shared)]).get("/mimir/pages").json()

        assert (local.list_calls, shared.list_calls) == (1, 1)
        assert {page["path"]: page["mounts"] for page in pages} == {
            "notes/a.md": ["local", "shared"],
            "notes/b.md": ["shared"],
        }

    def test_a_listing_of_one_mount_does_not_walk_the_others(self) -> None:
        tracker = {"in_flight": 0, "peak": 0}
        local = _Counting(tracker)
        shared = _Counting(tracker)
        _page(shared, "notes/b.md", "B")

        client = _client([("local", local), ("shared", shared)])
        pages = client.get("/mimir/pages", params={"mount": "shared"}).json()

        assert [page["mounts"] for page in pages] == [["shared"]]
        assert local.list_calls == 0

    def test_a_mount_that_cannot_list_fails_the_listing_by_name(self) -> None:
        """The attribution walk used to swallow this and drop the mount."""
        tracker = {"in_flight": 0, "peak": 0}
        client = _client([("local", _Counting(tracker)), ("kanuck", _BrokenListing())])

        with pytest.raises(MimirUnavailableError, match="'kanuck' failed during list_pages"):
            client.get("/mimir/pages")


class TestGraph:
    def test_mount_graphs_are_asked_for_concurrently(self) -> None:
        tracker = {"in_flight": 0, "peak": 0}
        mounts = [(name, _Counting(tracker)) for name in ("a", "b", "c")]
        for name, adapter in mounts:
            _page(adapter, f"{name}.md", name.upper())

        graph = _client(mounts).get("/mimir/graph").json()

        assert tracker["peak"] == 3
        assert {node["mount"] for node in graph["nodes"]} == {"a", "b", "c"}
