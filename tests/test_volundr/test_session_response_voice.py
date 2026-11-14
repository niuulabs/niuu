"""A session advertises its voice endpoint only while it runs a launch that serves the channel."""

import pytest
from pydantic import ValidationError

from niuu.domain.session_voice import SESSION_VOICE_PROTOCOL
from volundr.adapters.inbound.rest import SessionCreate, SessionResponse
from volundr.domain.models import Session, SessionStatus
from volundr.domain.session_launch_facts import LAUNCH_FACTS_KEY, VOICE_CHANNEL_FACT

_SESSION_ID = "5d1f3e53-1e0a-4a3c-9a55-0a1d0d8f4c11"
_VOICE = {LAUNCH_FACTS_KEY: {VOICE_CHANNEL_FACT: True}}


def _session(
    *,
    status: SessionStatus = SessionStatus.RUNNING,
    chat_endpoint: str | None = f"wss://forge.example.com/s/{_SESSION_ID}/session",
    workload_config: dict | None = None,
) -> Session:
    return Session(
        name="voiced",
        status=status,
        chat_endpoint=chat_endpoint,
        workload_config=_VOICE if workload_config is None else workload_config,
    )


def _voice_endpoint(session: Session) -> dict | None:
    return SessionResponse.from_session(session, people_count=1).model_dump()["voice_endpoint"]


@pytest.mark.parametrize(
    ("chat_endpoint", "url"),
    [
        (
            f"wss://forge.example.com/s/{_SESSION_ID}/session",
            f"https://forge.example.com/s/{_SESSION_ID}/voice",
        ),
        (
            f"ws://forge.example.com:8080/s/{_SESSION_ID}/session",
            f"http://forge.example.com:8080/s/{_SESSION_ID}/voice",
        ),
        (f"/s/{_SESSION_ID}/session", f"/s/{_SESSION_ID}/voice"),
    ],
)
def test_a_running_voiced_session_advertises_its_endpoint(chat_endpoint: str, url: str) -> None:
    endpoint = _voice_endpoint(_session(chat_endpoint=chat_endpoint))

    assert endpoint == {"kind": "voice", "protocol": SESSION_VOICE_PROTOCOL, "url": url}


def test_the_endpoint_follows_the_public_host_the_chat_endpoint_does() -> None:
    session = _session(chat_endpoint=f"ws://127.0.0.1:8080/s/{_SESSION_ID}/session")

    response = SessionResponse.from_session(session, people_count=1, public_host="forge.lan")

    assert response.chat_endpoint == f"ws://forge.lan:8080/s/{_SESSION_ID}/session"
    assert response.voice_endpoint is not None
    assert response.voice_endpoint.url == f"http://forge.lan:8080/s/{_SESSION_ID}/voice"


@pytest.mark.parametrize(
    "status",
    [status for status in SessionStatus if status != SessionStatus.RUNNING],
)
def test_a_session_that_is_not_running_has_no_voice_endpoint(status: SessionStatus) -> None:
    assert _voice_endpoint(_session(status=status)) is None


@pytest.mark.parametrize(
    "workload_config",
    [
        {},
        {LAUNCH_FACTS_KEY: {}},
        {LAUNCH_FACTS_KEY: {VOICE_CHANNEL_FACT: False}},
        {"voice_channel": True},
    ],
)
def test_a_session_whose_launch_did_not_serve_voice_has_no_endpoint(workload_config: dict) -> None:
    assert _voice_endpoint(_session(workload_config=workload_config)) is None


@pytest.mark.parametrize(
    "chat_endpoint",
    [None, "", f"wss://forge.example.com/s/{_SESSION_ID}/", "ftp://forge.example.com/s/x/session"],
)
def test_a_session_without_a_broker_chat_endpoint_has_no_voice_endpoint(
    chat_endpoint: str | None,
) -> None:
    assert _voice_endpoint(_session(chat_endpoint=chat_endpoint)) is None


def test_the_response_schema_documents_the_endpoint_as_nullable() -> None:
    schema = SessionResponse.model_json_schema()["properties"]["voice_endpoint"]

    assert "lexi-agent-service-v1" in schema["description"]
    assert {"type": "null"} in schema["anyOf"]


def test_a_create_request_cannot_carry_the_launch_record() -> None:
    with pytest.raises(ValidationError, match=LAUNCH_FACTS_KEY):
        SessionCreate(name="s", model="m", workload_config=_VOICE)
