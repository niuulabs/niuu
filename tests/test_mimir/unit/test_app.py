"""Tests for mimir.app — create_app and helper functions (NIU-577)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest import mock
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from mimir.app import _build_embed_fn, create_app
from mimir.config import MimirServiceConfig

# ---------------------------------------------------------------------------
# _build_embed_fn
# ---------------------------------------------------------------------------


def test_build_embed_fn_raises_when_sentence_transformers_missing() -> None:
    """Replaces a test asserting this returned None.

    Returning None dropped Mímir to keyword-only with one warning line. On the
    golden set that is the difference between P@5 0.995 and 0.468, and
    semantic recall 1.000 versus 0.000 — invisible, and by far the largest
    retrieval regression in the system.
    """
    import builtins

    from mimir.app import _build_embed_fn

    real_import = builtins.__import__

    def _no_sentence_transformers(name, *args, **kwargs):
        if name == "sentence_transformers":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    with mock.patch.object(builtins, "__import__", _no_sentence_transformers):
        with pytest.raises(RuntimeError, match="will not silently drop"):
            _build_embed_fn("all-MiniLM-L6-v2")


def test_build_embed_fn_uses_an_endpoint_without_any_local_model() -> None:
    """An OpenAI-compatible URL needs only httpx — no heavy dependency."""
    from mimir.app import _build_embed_fn

    embed = _build_embed_fn(
        "Qwen/Qwen3-Embedding-0.6B", base_url="https://brain.test/v1", api_key=""
    )

    assert callable(embed)


def test_build_embed_fn_returns_callable_when_available() -> None:
    mock_st = type("MockST", (), {})()

    class FakeModule:
        SentenceTransformer = mock_st.__class__

    with patch.dict("sys.modules", {"sentence_transformers": FakeModule}):  # type: ignore[arg-type]
        result = _build_embed_fn("all-MiniLM-L6-v2")
    # When the module is importable, a coroutine function is returned.
    assert callable(result)


# ---------------------------------------------------------------------------
# create_app
# ---------------------------------------------------------------------------


def test_create_app_returns_fastapi_app(tmp_path: Path) -> None:
    from fastapi import FastAPI

    config = MimirServiceConfig(path=str(tmp_path / "mimir"))
    app = create_app(config)
    assert isinstance(app, FastAPI)


def test_create_app_mounts_mimir_router(tmp_path: Path) -> None:
    config = MimirServiceConfig(path=str(tmp_path / "mimir"))
    app = create_app(config)
    with TestClient(app) as client:
        routes = client.get("/openapi.json").json()["paths"]
    assert any("/mimir" in r for r in routes)


def test_create_app_exposes_api_v1_mimir_routes(tmp_path: Path) -> None:
    config = MimirServiceConfig(path=str(tmp_path / "mimir"))
    app = create_app(config)
    with TestClient(app) as client:
        assert client.get("/api/v1/mimir/stats").status_code == 200
        assert client.get("/api/v1/mimir/mounts").status_code == 200
        assert client.get("/api/v1/mimir/mcp").status_code == 405


def test_create_app_exposes_settings_schema(tmp_path: Path) -> None:
    config = MimirServiceConfig(path=str(tmp_path / "mimir"), name="shared", role="shared")
    app = create_app(config)
    with TestClient(app) as client:
        response = client.get("/settings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["title"] == "Mimir"
    assert payload["sections"][0]["id"] == "service"


def test_create_app_exposes_mounted_mimir_settings_alias(tmp_path: Path) -> None:
    config = MimirServiceConfig(path=str(tmp_path / "mimir"), name="shared", role="shared")
    app = create_app(config)
    with TestClient(app) as client:
        response = client.get("/mimir/settings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["title"] == "Mimir"
    assert payload["sections"][0]["id"] == "service"


def test_create_app_exposes_api_v1_mimir_settings_alias(tmp_path: Path) -> None:
    config = MimirServiceConfig(path=str(tmp_path / "mimir"), name="shared", role="shared")
    app = create_app(config)
    with TestClient(app) as client:
        response = client.get("/api/v1/mimir/settings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["title"] == "Mimir"
    assert payload["sections"][0]["id"] == "service"


def test_create_app_uses_custom_search_db(tmp_path: Path) -> None:
    db_path = str(tmp_path / "custom_search.db")
    config = MimirServiceConfig(
        path=str(tmp_path / "mimir"),
        search_db=db_path,
    )
    # Should not raise even with a custom db path
    app = create_app(config)
    assert app is not None


def test_create_app_no_embedding_model_uses_fts_only(tmp_path: Path) -> None:
    config = MimirServiceConfig(
        path=str(tmp_path / "mimir"),
        embedding_model=None,
    )
    app = create_app(config)
    assert app is not None


# ---------------------------------------------------------------------------
# Lifespan — startup bootstraps an empty search index
# ---------------------------------------------------------------------------


def test_lifespan_rebuilds_search_index_on_startup(tmp_path: Path) -> None:
    config = MimirServiceConfig(path=str(tmp_path / "mimir"))
    app = create_app(config)

    # Using TestClient as context manager triggers lifespan startup/shutdown.
    with TestClient(app) as client:
        response = client.get("/mimir/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"


def test_lifespan_reuses_persisted_search_index(tmp_path: Path) -> None:
    from niuu.adapters.search.sqlite import SqliteSearchAdapter

    root = tmp_path / "mimir"
    search_db = root / "search.db"
    persisted = SqliteSearchAdapter(path=str(search_db))
    asyncio.run(persisted.index("doc-1", "persisted content", {}))

    app = create_app(MimirServiceConfig(path=str(root), search_db=str(search_db)))
    with patch(
        "mimir.adapters.markdown.MarkdownMimirAdapter.rebuild_search_index",
        new_callable=AsyncMock,
    ) as rebuild:
        with TestClient(app):
            pass

    rebuild.assert_not_awaited()


def test_create_app_exposes_health_aliases(tmp_path: Path) -> None:
    config = MimirServiceConfig(path=str(tmp_path / "mimir"), name="shared", role="shared")
    app = create_app(config)
    with TestClient(app) as client:
        root = client.get("/health")
        mounted = client.get("/mimir/health")
        api = client.get("/api/v1/mimir/health")

    assert root.status_code == 200
    assert mounted.status_code == 200
    assert api.status_code == 200
    assert root.json() == {"status": "healthy", "name": "shared", "role": "shared"}
    assert mounted.json() == root.json()
    assert api.json() == root.json()


def test_lifespan_handles_rebuild_failure_gracefully(tmp_path: Path) -> None:
    config = MimirServiceConfig(path=str(tmp_path / "mimir"))
    app = create_app(config)

    with patch(
        "mimir.adapters.markdown.MarkdownMimirAdapter.rebuild_search_index",
        new_callable=AsyncMock,
        side_effect=RuntimeError("index exploded"),
    ):
        # Startup should not raise even if rebuild fails
        with TestClient(app):
            pass


def test_lifespan_skips_announce_when_no_url(tmp_path: Path) -> None:
    config = MimirServiceConfig(path=str(tmp_path / "mimir"), announce_url=None)
    app = create_app(config)
    # No announce_url — lifespan should not attempt announcement
    with TestClient(app):
        pass


def test_lifespan_announce_url_exception_is_swallowed(tmp_path: Path) -> None:
    config = MimirServiceConfig(
        path=str(tmp_path / "mimir"),
        announce_url="http://sleipnir.local/announce",
    )
    app = create_app(config)
    # The import of _announce_mimir will fail in test (ravn not wired) — that's fine
    with TestClient(app):
        pass


@pytest.mark.asyncio
async def test_build_embed_fn_embed_callable_invokes_model(tmp_path: Path) -> None:
    class _FakeVector:
        """Minimal stand-in for a numpy array — only .tolist() is needed."""

        def tolist(self) -> list[float]:
            return [0.1, 0.2, 0.3]

    class FakeModel:
        def encode(self, text: str, **kwargs) -> _FakeVector:
            return _FakeVector()

    class FakeST:
        class SentenceTransformer:
            def __new__(cls, name: str) -> FakeModel:  # type: ignore[misc]
                return FakeModel()

    with patch.dict("sys.modules", {"sentence_transformers": FakeST}):  # type: ignore[arg-type]
        embed_fn = _build_embed_fn("all-MiniLM-L6-v2")

    assert callable(embed_fn)
    result = await embed_fn("test text")
    assert isinstance(result, list)
    assert len(result) == 3

    # Second call — model already loaded (exercises the cached branch).
    result2 = await embed_fn("another text")
    assert len(result2) == 3
