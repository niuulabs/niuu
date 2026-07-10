"""Tests for the standalone ``/s/{session_id}`` session-proxy mounting.

The K8s deployment runs Volundr via ``uvicorn volundr.main:create_app`` with no
CLI root app in front, so the app itself must terminate browser session
traffic. These tests drive ``register_session_proxy_routes()`` on a bare
FastAPI app — the standalone composition — through the same behaviors the
mini-mode root app is tested for in ``tests/test_cli/test_server.py``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.app import SkuldPortRegistry, register_session_proxy_routes
from niuu.ports.session_proxy import SessionProxyTarget


def _bare_app(tmp_path) -> tuple[FastAPI, SkuldPortRegistry]:
    reg = SkuldPortRegistry(state_file=tmp_path / "forge-state.json")
    app = FastAPI()
    register_session_proxy_routes(app, reg)
    return app, reg


def _external_target() -> SessionProxyTarget:
    return SessionProxyTarget(
        service_url="http://forge-123--skuld.openshell.localhost:8080",
        connect_host="openshell.openshell.svc.cluster.local",
        connect_port=8080,
    )


class TestStandaloneSessionProxyRoutes:
    """register_session_proxy_routes on a bare app (K8s standalone shape)."""

    def test_health_route_mounted_returns_session_not_found(self, tmp_path) -> None:
        # The K8s regression was FastAPI's default {"detail": "Not Found"}:
        # nothing mounted /s/{id} at all. The mounted route must answer with
        # the proxy's own "Session not found".
        app, _reg = _bare_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/s/nonexistent/health")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Session not found"

    def test_http_route_mounted_returns_session_not_found(self, tmp_path) -> None:
        app, _reg = _bare_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/s/nonexistent/api/conversation/history")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Session not found"

    def test_http_route_resolves_external_target_through_gateway(self, tmp_path) -> None:
        app, reg = _bare_app(tmp_path)
        target = _external_target()

        async def _resolve(session_id: str) -> SessionProxyTarget | None:
            return target if session_id == "sess-open" else None

        reg.set_target_resolver(_resolve)
        client = TestClient(app)

        mock_response = MagicMock()
        mock_response.content = b'{"ok": true}'
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client
            resp = client.get("/s/sess-open/api/conversation/history")

        assert resp.status_code == 200
        request = mock_client.request.await_args
        assert request.kwargs["url"] == (
            "http://openshell.openshell.svc.cluster.local:8080/api/conversation/history"
        )
        assert request.kwargs["headers"]["Host"] == "forge-123--skuld.openshell.localhost:8080"

    def test_ws_route_closes_4410_without_port_or_target(self, tmp_path) -> None:
        from starlette.websockets import WebSocketDisconnect

        app, _reg = _bare_app(tmp_path)
        client = TestClient(app)

        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/s/unknown/session"):
                pass

        assert exc.value.code == 4410

    def test_ws_route_dials_gateway_for_external_target(self, tmp_path) -> None:
        from starlette.websockets import WebSocketDisconnect

        app, reg = _bare_app(tmp_path)
        target = _external_target()

        async def _resolve(session_id: str) -> SessionProxyTarget | None:
            return target if session_id == "sess-open" else None

        reg.set_target_resolver(_resolve)
        client = TestClient(app)

        captured: dict = {}

        def _connect(url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            raise OSError("gateway unreachable")

        with patch("websockets.asyncio.client.connect", side_effect=_connect):
            with pytest.raises(WebSocketDisconnect) as exc:
                with client.websocket_connect("/s/sess-open/session") as ws:
                    ws.receive_text()

        # The broker leg failed, so the browser leg closes deterministically —
        # what matters here is the dial: the sandbox route stays in the URL
        # (its Host), while the TCP connection goes to the gateway address.
        assert exc.value.code == 4410
        assert captured["url"] == "ws://forge-123--skuld.openshell.localhost:8080/session"
        assert captured["kwargs"]["host"] == "openshell.openshell.svc.cluster.local"
        assert captured["kwargs"]["port"] == 8080
        assert captured["kwargs"]["proxy"] is None
