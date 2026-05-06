"""Integration tests for HttpPersonaAdapter against a real volundr REST app.

Uses a starlette TestClient (backed by a real FastAPI ASGI app with the
personas router) so no external network is required.  The adapter's
``_transport`` parameter is wired to a ``_SyncASGITransport`` that forwards
requests to the TestClient — giving us a genuine HTTP round-trip without a
running server.

Tests
-----
* Round-trip: persona created via REST → loadable by the adapter.
* ``list_names()`` reflects REST-created personas.
* 404 → ``None`` (persona not found).
* PAT auth end-to-end: MemoryTokenIssuer issues a real JWT; adapter sends it
  as ``Authorization: Bearer <token>``; ASGI middleware confirms receipt.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.testclient import TestClient

from ravn.adapters.personas.http import HttpPersonaAdapter
from ravn.adapters.personas.loader import FilesystemPersonaAdapter, PersonaConfig
from ravn.api.personas import create_personas_router

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _SyncASGITransport(httpx.BaseTransport):
    """Routes httpx.Client requests to a running starlette TestClient.

    Allows ``HttpPersonaAdapter`` (which uses ``httpx.Client``) to call a real
    ASGI app without a network server — the TestClient handles the sync↔async
    bridging internally via starlette's transport layer.
    """

    def __init__(self, tc: TestClient) -> None:
        self._tc = tc

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        content = request.read()
        # Strip headers that TestClient sets itself to avoid duplication.
        filtered = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in ("host", "content-length", "transfer-encoding")
        }
        resp = self._tc.request(
            method=request.method,
            url=str(request.url),
            content=content,
            headers=filtered,
        )
        return httpx.Response(
            status_code=resp.status_code,
            headers=dict(resp.headers),
            content=resp.content,
        )


class _IsolatedPersonaLoader(FilesystemPersonaAdapter):
    """Test-only adapter that saves personas to the first custom dir.

    ``FilesystemPersonaAdapter.save()`` always writes to ``~/.ravn/personas/``,
    which is not in the search path when ``persona_dirs`` is set explicitly.
    This subclass overrides ``save()`` so that both reads and writes go to the
    same temp directory — giving the integration tests a fully isolated store.
    """

    def __init__(self, personas_dir: str) -> None:
        super().__init__(persona_dirs=[personas_dir], include_builtin=False)
        self._write_dir = Path(personas_dir)

    def save(self, config: PersonaConfig) -> None:
        self._write_dir.mkdir(parents=True, exist_ok=True)
        dest = self._write_dir / f"{config.name}.yaml"
        dest.write_text(yaml.dump(config.to_dict(), allow_unicode=True), encoding="utf-8")


def _make_personas_app(personas_dir: str) -> FastAPI:
    """Build a FastAPI app with the personas router writing to *personas_dir*."""
    loader = _IsolatedPersonaLoader(personas_dir)
    app = FastAPI()
    app.include_router(create_personas_router(loader))
    return app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def personas_dir(tmp_path: Path) -> str:
    d = tmp_path / "personas"
    d.mkdir()
    return str(d)


@pytest.fixture()
def personas_app(personas_dir: str) -> FastAPI:
    return _make_personas_app(personas_dir)


# ---------------------------------------------------------------------------
# Round-trip: create via REST, load via adapter
# ---------------------------------------------------------------------------


def test_round_trip_load(personas_app: FastAPI) -> None:
    """Persona created via REST POST is loadable by HttpPersonaAdapter.load()."""
    with TestClient(personas_app) as tc:
        transport = _SyncASGITransport(tc)
        adapter = HttpPersonaAdapter(base_url="http://testserver", _transport=transport)

        resp = tc.post(
            "/api/v1/ravn/personas",
            json={
                "name": "integ-coder",
                "system_prompt_template": "You are an integration test coder.",
                "permission_mode": "workspace-write",
                "allowed_tools": ["file", "git"],
                "iteration_budget": 30,
            },
        )
        assert resp.status_code == 201, resp.text

        config = adapter.load("integ-coder")

        assert config is not None
        assert config.name == "integ-coder"
        assert config.system_prompt_template == "You are an integration test coder."
        assert config.permission_mode == "workspace-write"
        assert config.allowed_tools == ["file", "git"]
        assert config.iteration_budget == 30


def test_round_trip_list_names(personas_app: FastAPI) -> None:
    """list_names() returns personas that have been created via REST."""
    with TestClient(personas_app) as tc:
        transport = _SyncASGITransport(tc)
        adapter = HttpPersonaAdapter(base_url="http://testserver", _transport=transport)

        tc.post("/api/v1/ravn/personas", json={"name": "alpha-agent"})
        tc.post("/api/v1/ravn/personas", json={"name": "beta-agent"})

        names = adapter.list_names()

        assert "alpha-agent" in names
        assert "beta-agent" in names
        assert names == sorted(names), "list_names() must return a sorted list"


def test_load_returns_none_for_missing_persona(personas_app: FastAPI) -> None:
    """load() returns None when the persona does not exist in the registry."""
    with TestClient(personas_app) as tc:
        transport = _SyncASGITransport(tc)
        adapter = HttpPersonaAdapter(base_url="http://testserver", _transport=transport)

        config = adapter.load("no-such-persona")

        assert config is None


# ---------------------------------------------------------------------------
# PAT auth — end-to-end with real token issuance
# ---------------------------------------------------------------------------


class _CaptureAuthMiddleware(BaseHTTPMiddleware):
    """Middleware that records the Authorization header from each request."""

    def __init__(self, app, store: dict) -> None:
        super().__init__(app)
        self._store = store

    async def dispatch(self, request: Request, call_next):
        self._store["authorization"] = request.headers.get("authorization", "")
        return await call_next(request)


def test_pat_bearer_header_sent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Adapter sends Authorization: Bearer when RAVN_VOLUNDR_TOKEN env var is set."""
    received: dict[str, str] = {}

    personas_dir = str(tmp_path / "personas")
    Path(personas_dir).mkdir()

    loader = _IsolatedPersonaLoader(personas_dir)
    app = FastAPI()
    app.add_middleware(_CaptureAuthMiddleware, store=received)
    app.include_router(create_personas_router(loader))

    monkeypatch.setenv("RAVN_VOLUNDR_TOKEN", "simple-bearer-token")

    with TestClient(app) as tc:
        transport = _SyncASGITransport(tc)
        adapter = HttpPersonaAdapter(
            base_url="http://testserver",
            _transport=transport,
        )
        # 404 is fine — we only care that the auth header was sent.
        adapter.load("any-persona")

    assert received.get("authorization") == "Bearer simple-bearer-token"


def test_pat_real_issuance_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: MemoryTokenIssuer issues a real JWT; adapter sends it as Bearer."""
    from niuu.adapters.memory_token_issuer import MemoryTokenIssuer

    received: dict[str, str] = {}

    personas_dir = str(tmp_path / "personas")
    Path(personas_dir).mkdir()

    loader = _IsolatedPersonaLoader(personas_dir)
    app = FastAPI()
    app.add_middleware(_CaptureAuthMiddleware, store=received)
    app.include_router(create_personas_router(loader))

    issuer = MemoryTokenIssuer(signing_key="integ-test-signing-key-32bytes!!")
    issued = asyncio.run(
        issuer.issue_token(subject_token="any-subject-token", name="integ-test-pat")
    )
    raw_token = issued.raw_token

    monkeypatch.setenv("RAVN_VOLUNDR_TOKEN", raw_token)

    with TestClient(app) as tc:
        transport = _SyncASGITransport(tc)
        adapter = HttpPersonaAdapter(
            base_url="http://testserver",
            _transport=transport,
        )
        adapter.load("any-persona")

    assert received.get("authorization") == f"Bearer {raw_token}"


def test_no_auth_header_when_env_var_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No Authorization header is sent when the env var is not set."""
    received: dict[str, str] = {}

    personas_dir = str(tmp_path / "personas-no-auth")
    Path(personas_dir).mkdir()

    loader = _IsolatedPersonaLoader(personas_dir)
    app = FastAPI()
    app.add_middleware(_CaptureAuthMiddleware, store=received)
    app.include_router(create_personas_router(loader))

    monkeypatch.delenv("RAVN_VOLUNDR_TOKEN", raising=False)

    with TestClient(app) as tc:
        transport = _SyncASGITransport(tc)
        adapter = HttpPersonaAdapter(
            base_url="http://testserver",
            token_env="RAVN_VOLUNDR_TOKEN",
            _transport=transport,
        )
        adapter.load("some-persona")

    assert received.get("authorization", "") == ""
