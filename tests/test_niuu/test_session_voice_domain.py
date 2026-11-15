"""The names Forge and a session's Skuld agree on for the voice channel."""

import pytest

from niuu.domain.session_voice import (
    SESSION_VOICE_PATH,
    voice_client_secrets_path,
    voice_endpoint_from_chat,
)


@pytest.mark.parametrize(
    ("chat", "voice"),
    [
        ("ws://forge:8080/s/abc/session", "http://forge:8080/s/abc/voice"),
        ("wss://forge.example.com/s/abc/session", "https://forge.example.com/s/abc/voice"),
        ("wss://forge.example.com/s/abc/session/", "https://forge.example.com/s/abc/voice"),
        ("http://pod:8081/session", "http://pod:8081/voice"),
        (
            "https://gw.example.com/sessions/abc/session",
            "https://gw.example.com/sessions/abc/voice",
        ),
        ("/s/abc/session", "/s/abc/voice"),
        ("/session", "/voice"),
        (
            "wss://forge.example.com/s/abc/session?history=recent",
            "https://forge.example.com/s/abc/voice",
        ),
    ],
)
def test_the_voice_endpoint_sits_beside_the_chat_endpoint(chat: str, voice: str) -> None:
    assert voice_endpoint_from_chat(chat) == voice


@pytest.mark.parametrize(
    "chat",
    [
        None,
        "",
        "wss://forge.example.com/s/abc/",
        "wss://forge.example.com/s/abc/sessions",
        "https://s-abc.example.com/s/abc/",
        "ftp://forge.example.com/s/abc/session",
        "wsx://forge.example.com/s/abc/session",
    ],
)
def test_an_endpoint_that_is_not_a_brokers_chat_has_no_voice_endpoint(chat: str | None) -> None:
    assert voice_endpoint_from_chat(chat) is None


def test_the_voice_path_is_where_the_broker_mounts_the_channel() -> None:
    assert SESSION_VOICE_PATH == "/voice"


def test_the_minting_route_carries_the_session_id_and_forges_api_prefix() -> None:
    assert voice_client_secrets_path("abc") == "/api/v1/forge/sessions/abc/voice/client-secrets"
    assert voice_client_secrets_path("abc", prefix="/forge") == (
        "/forge/sessions/abc/voice/client-secrets"
    )


def test_a_session_id_cannot_climb_out_of_the_minting_route() -> None:
    assert voice_client_secrets_path("../x/y") == (
        "/api/v1/forge/sessions/..%2Fx%2Fy/voice/client-secrets"
    )
