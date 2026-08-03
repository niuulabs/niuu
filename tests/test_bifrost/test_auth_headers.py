"""Attribution headers reach the usage store whichever spelling is used.

Ravn's Bifröst adapter sent `X-Ravn-Agent-Id` from the day it was written and
this side has always read `x-agent-id`, so every request in the estate was
recorded against `anonymous` — a usage store with a column for the caller that
had never once seen one.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from bifrost.adapters.auth.open import OpenAuthAdapter


@pytest.fixture
def identity_app() -> FastAPI:
    app = FastAPI()
    adapter = OpenAuthAdapter()

    @app.get("/who")
    def who(request: Request) -> dict[str, str]:
        identity = adapter.extract(request)
        return {
            "agentId": identity.agent_id,
            "tenantId": identity.tenant_id,
            "sessionId": identity.session_id,
            "sagaId": identity.saga_id,
        }

    return app


def test_the_plain_header_names_identify_the_caller(identity_app: FastAPI) -> None:
    body = (
        TestClient(identity_app)
        .get(
            "/who",
            headers={
                "X-Agent-Id": "muninn",
                "X-Tenant-Id": "niuu",
                "X-Session-Id": "sess-1",
                "X-Saga-Id": "saga-1",
            },
        )
        .json()
    )

    assert body == {
        "agentId": "muninn",
        "tenantId": "niuu",
        "sessionId": "sess-1",
        "sagaId": "saga-1",
    }


def test_the_ravn_prefixed_names_identify_the_caller_too(identity_app: FastAPI) -> None:
    """Every deployed Ravn sends these, and they were being thrown away."""
    body = (
        TestClient(identity_app)
        .get(
            "/who",
            headers={"X-Ravn-Agent-Id": "bryn", "X-Ravn-Session-Id": "sess-2"},
        )
        .json()
    )

    assert body["agentId"] == "bryn"
    assert body["sessionId"] == "sess-2"


def test_the_plain_name_wins_when_both_are_sent(identity_app: FastAPI) -> None:
    body = (
        TestClient(identity_app)
        .get(
            "/who",
            headers={"X-Agent-Id": "canonical", "X-Ravn-Agent-Id": "legacy"},
        )
        .json()
    )

    assert body["agentId"] == "canonical"


def test_a_caller_that_says_nothing_is_anonymous(identity_app: FastAPI) -> None:
    body = TestClient(identity_app).get("/who").json()

    assert body["agentId"] == "anonymous"
    assert body["tenantId"] == "default"
