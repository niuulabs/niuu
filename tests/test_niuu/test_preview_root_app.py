"""The mini-mode root app serves live previews on ``*.localhost`` (niuu.app)."""

from __future__ import annotations

import httpx

from cli.registry import PluginRegistry
from niuu.app import build_root_app
from niuu.config import NiuuSettings
from niuu.config_models import SessionPreviewConfig
from niuu.preview_proxy import PreviewRouter, PreviewRouterMiddleware
from niuu.session_proxy import SkuldPortRegistry

SESSION = "0a1b2c3d-4e5f-6789-abcd-ef0123456789"


def _root_app(tmp_path, monkeypatch, preview: SessionPreviewConfig | None = None):
    registry = SkuldPortRegistry(tmp_path / "state.json", dev_identity=True)
    if preview is not None:
        real_settings = NiuuSettings

        def settings_with_preview() -> NiuuSettings:
            settings = real_settings()
            settings.host.session_preview = preview
            return settings

        monkeypatch.setattr("niuu.app.NiuuSettings", settings_with_preview)
    app = build_root_app(
        registry=PluginRegistry(),
        host="127.0.0.1",
        port=8080,
        enabled_mounts={"skuld-proxy"},
        skuld_registry=registry,
    )
    return app, registry


def _preview_middleware(app) -> list:
    return [m for m in app.user_middleware if m.cls is PreviewRouterMiddleware]


class TestRootAppPreview:
    def test_enabled_by_default_as_the_outermost_middleware(self, tmp_path, monkeypatch):
        app, _ = _root_app(tmp_path, monkeypatch)

        # add_middleware prepends: index 0 is the outermost layer.
        assert app.user_middleware[0].cls is PreviewRouterMiddleware
        assert app.user_middleware[0].kwargs["domain"] == "localhost"
        assert isinstance(app.user_middleware[0].kwargs["router_provider"](), PreviewRouter)

    def test_the_configured_domain_is_used(self, tmp_path, monkeypatch):
        config = SessionPreviewConfig(enabled=True, domain="dev.localhost")

        app, _ = _root_app(tmp_path, monkeypatch, config)

        assert app.user_middleware[0].kwargs["domain"] == "dev.localhost"

    def test_disabled_adds_no_middleware(self, tmp_path, monkeypatch):
        app, _ = _root_app(tmp_path, monkeypatch, SessionPreviewConfig(enabled=False))

        assert _preview_middleware(app) == []

    async def test_a_preview_host_reaches_the_router_not_the_platform(self, tmp_path, monkeypatch):
        app, registry = _root_app(tmp_path, monkeypatch)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=f"http://3000--{SESSION}.localhost:8080",
        ) as client:
            response = await client.get("/")

        # The router answered: the session is not running, in the router's own words.
        assert response.status_code == 404
        assert response.text == "This session is not running."
        assert registry.get_port(SESSION) is None

    async def test_platform_hosts_still_reach_the_platform(self, tmp_path, monkeypatch):
        app, _ = _root_app(tmp_path, monkeypatch)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost:8080"
        ) as client:
            response = await client.get("/s/nonexistent/health")

        assert response.status_code == 404
        assert response.json()["detail"] == "Session not found"
