"""Tests for the explicit Volundr workflow-session A2A publication path."""

from volundr.adapters.inbound.rest import SessionResponse
from volundr.domain.models import Session


def test_session_response_exposes_only_explicit_safe_a2a_fields() -> None:
    session = Session(
        name="addressable-workflow",
        workload_config={
            "a2aCardUrl": "https://agent.example.test/.well-known/agent-card.json",
            "a2aEndpointUrl": "https://agent.example.test/a2a",
            "environmentId": "environment-a",
            "a2aVisibility": "tenant",
            "clientSecret": "must-not-leak",
        },
    )

    payload = SessionResponse.from_session(session).model_dump(by_alias=True)

    assert payload["a2aCardUrl"].endswith("agent-card.json")
    assert payload["a2aEndpointUrl"] == "https://agent.example.test/a2a"
    assert payload["environmentId"] == "environment-a"
    assert payload["a2aVisibility"] == "tenant"
    assert "clientSecret" not in payload
    assert "workload_config" not in payload


def test_session_response_rejects_credential_bearing_or_non_http_a2a_urls() -> None:
    session = Session(
        name="unsafe-workflow",
        workload_config={
            "a2a_card_url": "https://user:password@agent.example.test/card.json",
            "a2a_endpoint_url": "file:///tmp/a2a.sock",
        },
    )

    payload = SessionResponse.from_session(session).model_dump(by_alias=True)

    assert payload["a2aCardUrl"] is None
    assert payload["a2aEndpointUrl"] is None
    assert payload["a2aVisibility"] == "user"
