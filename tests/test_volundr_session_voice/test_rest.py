"""POST /sessions/{id}/voice/client-secrets: who may mint, when, and how failures map."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.domain.services.forge_session_token import ForgeSessionClaims
from niuu.domain.services.token_scope import FORGE_SESSION_VOICE_SCOPE
from tests.support.forge_session import OWNER, PREFIX, ForgeApp, bearer, build_forge_app
from volundr.adapters.inbound.rest_session_voice import create_session_voice_router
from volundr.domain.models import Session, SessionStatus
from volundr.domain.services.session_voice import (
    SessionVoiceCredentialService,
    VoiceProviderBinding,
)
from volundr.domain.session_launch_facts import (
    LAUNCH_FACTS_KEY,
    VOICE_CHANNEL_FACT,
    voice_channel_launched,
)
from volundr.ports.realtime_voice import (
    RealtimeCredential,
    RealtimeCredentialError,
    RealtimeCredentialMinterPort,
)

BODY = {"provider": "openai", "model": "gpt-realtime", "voice": "marin", "instructions": "Hi."}


class FakeStore:
    def __init__(self, values: dict[str, dict[str, str]]) -> None:
        self.values = values

    async def get_value(self, owner_type: str, owner_id: str, name: str) -> dict[str, str] | None:
        if owner_type != "user" or owner_id != OWNER:
            return None
        return self.values.get(name)


class FakeMinter(RealtimeCredentialMinterPort):
    provider = "openai"

    def __init__(self) -> None:
        self.error: Exception | None = None
        self.calls: list[dict[str, Any]] = []

    async def mint(self, **kwargs: Any) -> RealtimeCredential:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return RealtimeCredential(
            token="ek_secret",
            expires_at_ms=1_700_000_000_000,
            model="gpt-realtime",
            session_bound=True,
        )


class Voice:
    def __init__(self, forge: ForgeApp, minter: FakeMinter, service: Any) -> None:
        self.forge = forge
        self.minter = minter
        self.service = service

    def url(self, session_id: UUID) -> str:
        return f"{PREFIX}/sessions/{session_id}/voice/client-secrets"

    async def started(self, *, voice: bool = True, owner: str = OWNER) -> tuple[Session, str]:
        session = await self.forge.session("s", owner=owner)
        credential = await self.forge.start(session)
        stored = await self.forge.sessions.get(session.id)
        config = dict(stored.workload_config)
        if voice:
            config[LAUNCH_FACTS_KEY] = {VOICE_CHANNEL_FACT: True}
        await self.forge.sessions.update(stored.model_copy(update={"workload_config": config}))
        return session, credential.token

    def post(self, session_id: UUID, token: str | None, body: dict | None = None):
        headers = bearer(token) if token else {}
        return self.forge.client.post(self.url(session_id), json=body or BODY, headers=headers)


def _build(*, service: bool = True) -> Voice:
    forge = build_forge_app()
    minter = FakeMinter()
    voice_service = (
        SessionVoiceCredentialService(
            FakeStore({"openai-realtime": {"api_key": "sk-owner"}}),  # type: ignore[arg-type]
            {"openai": VoiceProviderBinding(minter, "openai-realtime", "api_key")},
        )
        if service
        else None
    )
    forge.app.include_router(create_session_voice_router(voice_service, forge.session_service))
    return Voice(forge, minter, voice_service)


@pytest.fixture
def voice() -> Voice:
    return _build()


async def test_mints_the_owners_credential_with_no_store(voice: Voice) -> None:
    session, token = await voice.started()
    response = voice.post(session.id, token)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "provider": "openai",
        "token": "ek_secret",
        "expires_at_ms": 1_700_000_000_000,
        "model": "gpt-realtime",
        "session_bound": True,
    }
    assert voice.minter.calls == [
        {
            "api_key": "sk-owner",
            "model": "gpt-realtime",
            "voice": "marin",
            "instructions": "Hi.",
            "reasoning": "",
        }
    ]


async def test_503_without_a_service() -> None:
    voice = _build(service=False)
    session, token = await voice.started()
    response = voice.post(session.id, token)
    assert response.status_code == 503
    assert "voice.enabled" in response.json()["detail"]


async def test_403_without_a_forge_session_credential(voice: Voice) -> None:
    session, _ = await voice.started()
    response = voice.post(session.id, None)
    assert response.status_code == 403
    assert "Only the session's own Forge session credential" in response.json()["detail"]
    assert voice.minter.calls == []


async def test_403_for_another_sessions_credential(voice: Voice) -> None:
    first, _ = await voice.started()
    _, other_token = await voice.started()
    response = voice.post(first.id, other_token)
    assert response.status_code == 403
    assert "another session" in response.json()["detail"]
    assert voice.minter.calls == []


async def test_a_deleted_session_revokes_its_credential(voice: Voice) -> None:
    session, token = await voice.started()
    await voice.forge.sessions.delete(session.id)
    response = voice.post(session.id, token)
    assert response.status_code == 401


async def test_404_when_the_session_is_not_found_past_authentication(voice: Voice) -> None:
    missing = UUID(int=7)
    # An unauthenticated caller reaches the route, then is refused for lack of claims; a
    # missing session is only reachable past the credential checks, so drive the handler
    # with the claims the middleware would have attached.
    app = FastAPI()

    @app.middleware("http")
    async def attach(request, call_next):
        request.state.forge_session = ForgeSessionClaims(
            session_id=str(missing),
            launch_id="l",
            owner_id=OWNER,
            tenant_id="t",
            email="",
            scopes=(FORGE_SESSION_VOICE_SCOPE,),
            expires_at=0,
            token_id="x",
        )
        return await call_next(request)

    app.include_router(create_session_voice_router(voice.service, voice.forge.session_service))
    response = TestClient(app).post(voice.url(missing), json=BODY)
    assert response.status_code == 404
    assert str(missing) in response.json()["detail"]


async def test_409_when_the_launch_runs_no_voice_channel(voice: Voice) -> None:
    session, token = await voice.started(voice=False)
    response = voice.post(session.id, token)
    assert response.status_code == 409
    assert "restart it with voice.enabled" in response.json()["detail"]
    assert voice.minter.calls == []


async def test_409_when_the_session_has_no_owner(voice: Voice) -> None:
    session, _ = await voice.started()
    stored = await voice.forge.sessions.get(session.id)
    assert voice_channel_launched(stored.workload_config)
    ownerless = SimpleNamespace(workload_config=stored.workload_config, owner_id=None)

    class Sessions:
        async def get_session(self, session_id: UUID) -> Any:
            return ownerless

    app = FastAPI()

    @app.middleware("http")
    async def attach(request, call_next):
        request.state.forge_session = ForgeSessionClaims(
            session_id=str(session.id),
            launch_id="l",
            owner_id=OWNER,
            tenant_id="t",
            email="",
            scopes=(FORGE_SESSION_VOICE_SCOPE,),
            expires_at=0,
            token_id="x",
        )
        return await call_next(request)

    app.include_router(create_session_voice_router(voice.service, Sessions()))  # type: ignore[arg-type]
    response = TestClient(app).post(voice.url(session.id), json=BODY)
    assert response.status_code == 409
    assert "no owner" in response.json()["detail"]


async def test_422_for_an_unconfigured_provider(voice: Voice) -> None:
    session, token = await voice.started()
    response = voice.post(session.id, token, {**BODY, "provider": "gemini"})
    assert response.status_code == 422
    assert "gemini" in response.json()["detail"]
    assert "openai" in response.json()["detail"]


@pytest.mark.parametrize(
    "body",
    [
        {**BODY, "provider": ""},
        {**BODY, "model": ""},
        {**BODY, "surprise": 1},
        {"model": "m"},
    ],
)
async def test_422_for_a_malformed_body(voice: Voice, body: dict) -> None:
    session, token = await voice.started()
    response = voice.post(session.id, token, body)
    assert response.status_code == 422
    assert voice.minter.calls == []


async def test_409_when_the_owner_has_no_key(voice: Voice) -> None:
    session, token = await voice.started(owner="someone-else")
    response = voice.post(session.id, token)
    assert response.status_code == 409
    assert "store a user credential named 'openai-realtime'" in response.json()["detail"]
    assert voice.minter.calls == []


async def test_502_when_the_provider_fails(voice: Voice) -> None:
    session, token = await voice.started()
    voice.minter.error = RealtimeCredentialError("openai realtime mint returned HTTP 401: bad key")
    response = voice.post(session.id, token)
    assert response.status_code == 502
    assert response.json()["detail"] == "openai realtime mint returned HTTP 401: bad key"
    assert "ek_secret" not in response.text
    assert "sk-owner" not in response.text


async def test_a_stopped_session_is_refused_at_the_edge(voice: Voice) -> None:
    session, token = await voice.started()
    await voice.forge.set_status(session.id, SessionStatus.STOPPED)
    assert voice.post(session.id, token).status_code == 401
