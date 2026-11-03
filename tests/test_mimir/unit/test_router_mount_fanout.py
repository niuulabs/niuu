"""Aggregate Mímir reads across mounts: concurrent, single-pass, failures loud.

Measured on yggdrasil (2026-11-03): ``/mimir/graph`` cost the sum of eight
mounts' graphs, ``/mimir/pages`` listed every mount twice, and one gbrain
mount with no lint made ``/mimir/lint`` answer 501 for every Mímir mount.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.adapters.identity import EnvoyHeaderAuthenticationAdapter
from mimir.adapters.markdown import MarkdownMimirAdapter
from mimir.router import MimirRouter
from niuu.ports.mimir import MimirPort
from ravn.adapters.mimir.composite import CompositeMimirAdapter
from ravn.domain.exceptions import MimirUnavailableError
from ravn.domain.mimir import MimirMount, WriteRouting

_HEADERS = {
    "x-auth-user-id": "test-user",
    "x-auth-tenant": "default",
    "x-auth-roles": "volundr:admin",
}


class _CountingMarkdown(MarkdownMimirAdapter):
    """A real wiki that counts listings and how many reads overlap."""

    def __init__(self, root: Path, tracker: dict[str, int]) -> None:
        super().__init__(root=root)
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


class _NoLint(MarkdownMimirAdapter):
    """A backend that, like gbrain, has no lint."""

    async def lint(self, fix: bool = False):  # noqa: ANN201
        raise NotImplementedError("gbrain has no equivalent for lint.")


class _BrokenLint(MarkdownMimirAdapter):
    async def lint(self, fix: bool = False):  # noqa: ANN201
        raise RuntimeError("connection refused")


class _BrokenListing(MarkdownMimirAdapter):
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


def _page(root: Path, path: str, title: str) -> None:
    target = root / "wiki" / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"# {title}\n\nAbout {title}.\n")


class TestPages:
    def test_an_aggregate_listing_asks_each_mount_once(self, tmp_path: Path) -> None:
        tracker = {"in_flight": 0, "peak": 0}
        local = _CountingMarkdown(tmp_path / "local", tracker)
        shared = _CountingMarkdown(tmp_path / "shared", tracker)
        _page(tmp_path / "local", "notes/a.md", "A")
        _page(tmp_path / "shared", "notes/a.md", "A")
        _page(tmp_path / "shared", "notes/b.md", "B")

        pages = _client([("local", local), ("shared", shared)]).get("/mimir/pages").json()

        assert (local.list_calls, shared.list_calls) == (1, 1)
        assert {page["path"]: page["mounts"] for page in pages} == {
            "notes/a.md": ["local", "shared"],
            "notes/b.md": ["shared"],
        }

    def test_a_listing_of_one_mount_does_not_walk_the_others(self, tmp_path: Path) -> None:
        tracker = {"in_flight": 0, "peak": 0}
        local = _CountingMarkdown(tmp_path / "local", tracker)
        shared = _CountingMarkdown(tmp_path / "shared", tracker)
        _page(tmp_path / "shared", "notes/b.md", "B")

        client = _client([("local", local), ("shared", shared)])
        pages = client.get("/mimir/pages", params={"mount": "shared"}).json()

        assert [page["mounts"] for page in pages] == [["shared"]]
        assert local.list_calls == 0

    def test_a_mount_that_cannot_list_fails_the_listing_by_name(self, tmp_path: Path) -> None:
        """The attribution walk used to swallow this and drop the mount."""
        tracker = {"in_flight": 0, "peak": 0}
        client = _client(
            [
                ("local", _CountingMarkdown(tmp_path / "local", tracker)),
                ("kanuck", _BrokenListing(root=tmp_path / "kanuck")),
            ]
        )

        with pytest.raises(MimirUnavailableError, match="'kanuck' failed during list_pages"):
            client.get("/mimir/pages")


class TestGraph:
    def test_mount_graphs_are_asked_for_concurrently(self, tmp_path: Path) -> None:
        tracker = {"in_flight": 0, "peak": 0}
        mounts = [(name, _CountingMarkdown(tmp_path / name, tracker)) for name in ("a", "b", "c")]
        for name, _ in mounts:
            _page(tmp_path / name, f"{name}.md", name.upper())

        graph = _client(mounts).get("/mimir/graph").json()

        assert tracker["peak"] == 3
        assert {node["mount"] for node in graph["nodes"]} == {"a", "b", "c"}

    def test_markdown_graph_matches_the_port_projection(self, tmp_path: Path) -> None:
        root = tmp_path / "wiki-root"
        adapter = MarkdownMimirAdapter(root=root)
        _page(root, "people/ada.md", "Ada")
        (root / "wiki" / "people" / "bob.md").write_text("# Bob\n\nWorks with [[Ada]].\n")

        fast = asyncio.run(adapter.get_graph())
        default = asyncio.run(MimirPort.get_graph(adapter))

        assert fast == default
        assert len(fast.edges) == 1


class TestLintAcrossMounts:
    def test_a_mount_without_lint_is_reported_while_the_others_are_linted(
        self, tmp_path: Path
    ) -> None:
        _page(tmp_path / "shared", "notes/a.md", "A")
        client = _client(
            [
                ("shared", MarkdownMimirAdapter(root=tmp_path / "shared")),
                ("gbrain-kanuck", _NoLint(root=tmp_path / "kanuck")),
            ]
        )

        response = client.get("/mimir/lint")

        assert response.status_code == 200
        body = response.json()
        assert body["pages_checked"] == 1
        assert {issue["mount"] for issue in body["issues"]} <= {"shared"}
        mounts = {mount["name"]: mount for mount in body["mounts"]}
        assert mounts["shared"]["status"] == "linted"
        assert mounts["shared"]["pages_checked"] == 1
        assert mounts["shared"]["issues"] == len(body["issues"])
        assert mounts["gbrain-kanuck"] == {
            "name": "gbrain-kanuck",
            "status": "unsupported",
            "pages_checked": 0,
            "issues": 0,
            "detail": "gbrain has no equivalent for lint.",
        }

    def test_fix_and_reassign_report_the_unsupported_mount_too(self, tmp_path: Path) -> None:
        _page(tmp_path / "shared", "notes/a.md", "A")
        client = _client(
            [
                ("shared", MarkdownMimirAdapter(root=tmp_path / "shared")),
                ("gbrain-kanuck", _NoLint(root=tmp_path / "kanuck")),
            ]
        )

        fixed = client.post("/mimir/lint/fix")
        reassigned = client.post(
            "/mimir/lint/reassign", json={"issue_ids": [], "assignee": "ravn-a"}
        )

        for response in (fixed, reassigned):
            assert response.status_code == 200
            statuses = {m["name"]: m["status"] for m in response.json()["mounts"]}
            assert statuses == {"shared": "linted", "gbrain-kanuck": "unsupported"}

    def test_naming_the_unsupported_mount_is_still_501(self, tmp_path: Path) -> None:
        client = _client(
            [
                ("shared", MarkdownMimirAdapter(root=tmp_path / "shared")),
                ("gbrain-kanuck", _NoLint(root=tmp_path / "kanuck")),
            ]
        )

        response = client.get("/mimir/lint", params={"mount": "gbrain-kanuck"})

        assert response.status_code == 501
        assert response.json() == {"detail": "gbrain has no equivalent for lint."}

    def test_a_mount_that_fails_to_lint_still_fails_the_call(self, tmp_path: Path) -> None:
        """Unsupported is reported; broken is not quietly left out of a clean report."""
        client = _client(
            [
                ("shared", MarkdownMimirAdapter(root=tmp_path / "shared")),
                ("mimir-ui", _BrokenLint(root=tmp_path / "ui")),
            ]
        )

        response = client.get("/mimir/lint")

        assert response.status_code == 503
        assert "'mimir-ui' failed during lint: connection refused" in response.json()["detail"]
