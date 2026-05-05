"""Standalone Mímir FastAPI application.

Used when running Mímir as an independent service (``python -m mimir serve``).
The same ``MimirRouter`` can also be mounted on the existing Ravn gateway
(``ravn listen-mimir``) without any code changes.

Usage (standalone)::

    from mimir.app import create_app
    from mimir.config import MimirServiceConfig
    import uvicorn

    config = MimirServiceConfig(path="~/.ravn/mimir", name="shared", role="shared")
    app = create_app(config)
    uvicorn.run(app, host=config.host, port=config.port)
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from mimir.adapters.markdown import MarkdownMimirAdapter
from mimir.config import MimirServiceConfig
from mimir.mcp import MimirMcpServer
from mimir.registry import MimirRegistryStore
from mimir.router import MimirRouter
from niuu.settings_schema import (
    SettingsFieldSchema,
    SettingsProviderSchema,
    SettingsSectionSchema,
)

logger = logging.getLogger(__name__)


def _build_embed_fn(model_name: str):  # type: ignore[return]
    """Return an async embed function backed by sentence-transformers.

    The model is loaded lazily on the first call and cached.  If
    sentence-transformers is not installed the function returns ``None`` and
    the adapter falls back to FTS-only search.
    """
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore[import]
    except ImportError:
        logger.warning(
            "mimir: sentence-transformers not installed — "
            "falling back to FTS-only search (embedding_model=%r ignored)",
            model_name,
        )
        return None

    _model: SentenceTransformer | None = None

    async def _embed(text: str) -> list[float]:
        nonlocal _model
        import asyncio

        if _model is None:
            _model = await asyncio.to_thread(SentenceTransformer, model_name)
        vector = await asyncio.to_thread(_model.encode, text, normalize_embeddings=True)
        return vector.tolist()

    return _embed


def create_app(config: MimirServiceConfig) -> FastAPI:
    """Create the standalone Mímir FastAPI application.

    Args:
        config: Service configuration (path, host, port, name, role).

    Returns:
        A configured FastAPI application with the Mímir router mounted at
        ``/mimir``.
    """
    from niuu.adapters.search.sqlite import SqliteSearchAdapter

    search_db = config.search_db or str(Path(config.path).expanduser() / "search.db")
    embed_fn = _build_embed_fn(config.embedding_model) if config.embedding_model else None
    search_port = SqliteSearchAdapter(path=search_db, embed_fn=embed_fn)

    adapter = MarkdownMimirAdapter(root=config.path, search_port=search_port)
    registry_store = MimirRegistryStore(Path(config.path).expanduser() / ".mimir-registry.json")
    registry_store.ensure_entry(
        name=config.name,
        role=config.role,
        kind="local" if not config.announce_url else "remote",
        path=str(Path(config.path).expanduser()),
        url=config.announce_url or "",
        categories=config.categories,
        default_read_priority=0,
        desc="Current Mimir service instance",
    )
    mimir_router = MimirRouter(
        adapter=adapter,
        name=config.name,
        role=config.role,
        registry_store=registry_store,
    )
    mcp_server = MimirMcpServer(adapter=adapter, name=config.name)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Rebuild the search index from the filesystem on startup.
        try:
            n = await adapter.rebuild_search_index()
            logger.info("mimir[%s]: search index ready (%d pages)", config.name, n)
        except Exception as exc:  # noqa: BLE001
            logger.warning("mimir[%s]: search index rebuild failed: %s", config.name, exc)

        if config.announce_url:
            logger.info(
                "mimir[%s]: announcing at %s (role=%s)",
                config.name,
                config.announce_url,
                config.role,
            )
            try:
                from ravn.adapters.mesh.sleipnir_mesh import _announce_mimir  # type: ignore[import]

                await _announce_mimir(
                    name=config.name,
                    url=config.announce_url,
                    role=config.role,
                    categories=config.categories,
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("mimir: sleipnir announce skipped (%s)", exc)
        yield

    app = FastAPI(
        title=f"Mímir — {config.name}",
        description=(
            "Standalone Mímir knowledge service. "
            f"Role: {config.role}. "
            "Exposes the Mímir wiki over HTTP for Ravens, Valkyries, and Pi room nodes."
        ),
        version="1.0.0",
        docs_url="/mimir/docs",
        redoc_url=None,
        lifespan=lifespan,
    )

    app.include_router(mimir_router.router, prefix="/mimir")
    app.include_router(mcp_server.router(), prefix="/mcp")
    app.state.mimir_config = config

    @app.get("/settings", response_model=SettingsProviderSchema)
    async def settings() -> SettingsProviderSchema:
        categories = ", ".join(config.categories or ["all"])
        return SettingsProviderSchema(
            title="Mimir",
            subtitle="knowledge system settings",
            scope="service",
            sections=[
                SettingsSectionSchema(
                    id="service",
                    label="Service",
                    description=(
                        "Mounted Mimir instance characteristics exposed by the "
                        "current host profile."
                    ),
                    fields=[
                        SettingsFieldSchema(
                            key="instance_name",
                            label="Instance Name",
                            type="text",
                            value=config.name,
                            read_only=True,
                        ),
                        SettingsFieldSchema(
                            key="role",
                            label="Role",
                            type="text",
                            value=config.role,
                            read_only=True,
                        ),
                        SettingsFieldSchema(
                            key="knowledge_path",
                            label="Knowledge Path",
                            type="text",
                            value=config.path,
                            read_only=True,
                        ),
                        SettingsFieldSchema(
                            key="category_scope",
                            label="Category Scope",
                            type="text",
                            value=categories,
                            read_only=True,
                        ),
                        SettingsFieldSchema(
                            key="embedding_model",
                            label="Embedding Model",
                            type="text",
                            value=config.embedding_model or "fts-only",
                            read_only=True,
                        ),
                        SettingsFieldSchema(
                            key="announce_url",
                            label="Announce URL",
                            type="text",
                            value=config.announce_url or "disabled",
                            read_only=True,
                        ),
                    ],
                )
            ],
        )

    return app
