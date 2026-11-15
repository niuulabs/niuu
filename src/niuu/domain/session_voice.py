"""What Forge and a session's Skuld agree on about the session voice channel.

A running Forge session can hold a realtime voice call with its owner: the phone runs the
realtime model and talks to the provider directly, while the session's Skuld broker serves
the agent-service wire the phone speaks (``lexi-agent-service-v1``) under
:data:`SESSION_VOICE_PATH`. Forge mints the call's short-lived provider credential from
the owner's own provider key, so that key never enters the session.

This module holds the shared names only: the protocol, where the channel is mounted, the
Forge route the broker mints through, and how a session's voice endpoint is derived from
its chat endpoint. The channel itself is ``skuld.voice``; the minting is Forge's
(``volundr.domain.services.session_voice``).
"""

from __future__ import annotations

from urllib.parse import quote, urlsplit, urlunsplit

#: The wire the channel speaks: the Lexi app's agent-service protocol (a
#: ``{type, id, payload}`` WebSocket at the root plus REST under ``/api``).
SESSION_VOICE_PROTOCOL = "lexi-agent-service-v1"

#: Where a session's broker serves the channel: the WebSocket at ``/voice`` and the REST
#: routes under ``/voice/api``. It sits beside the chat WebSocket (``/session``) on the
#: same authenticated surface.
SESSION_VOICE_PATH = "/voice"

#: The broker's chat WebSocket path, which every session chat endpoint ends with.
SESSION_CHAT_PATH = "/session"

#: The endpoint kind a session advertises its channel under.
SESSION_VOICE_ENDPOINT_KIND = "voice"

#: The Forge route (under ``/api/v1/forge``) a session's broker mints a call's provider
#: credential through, with its own ``forge_session`` credential.
SESSION_VOICE_CLIENT_SECRETS_TEMPLATE = "/sessions/{session_id}/voice/client-secrets"

_HTTP_SCHEMES = {"ws": "http", "wss": "https", "http": "http", "https": "https"}


def voice_client_secrets_path(session_id: str, *, prefix: str = "/api/v1/forge") -> str:
    """The minting route of one session, with Forge's API prefix."""
    return prefix + SESSION_VOICE_CLIENT_SECRETS_TEMPLATE.format(
        session_id=quote(str(session_id), safe="")
    )


def voice_endpoint_from_chat(chat_endpoint: str | None) -> str | None:
    """The session's voice endpoint, derived from its chat endpoint.

    The chat endpoint is the broker's ``/session`` WebSocket however the session is
    reached (Forge's session proxy, a gateway route, an OpenShell service URL); the
    voice channel sits beside it at ``/voice`` on the same broker, so its address is the
    chat endpoint with that last segment swapped and a WebSocket scheme turned into
    its HTTP one (the app speaks REST to the endpoint and opens the WebSocket on it).
    A relative endpoint stays relative. ``None`` when the chat endpoint is not a
    broker's ``/session`` (no broker, so no voice channel).
    """
    if not chat_endpoint:
        return None
    parsed = urlsplit(chat_endpoint)
    path = parsed.path.rstrip("/")
    if not path.endswith(SESSION_CHAT_PATH):
        return None
    voice_path = path[: -len(SESSION_CHAT_PATH)] + SESSION_VOICE_PATH
    if not parsed.scheme:
        return urlunsplit(("", parsed.netloc, voice_path, "", ""))
    scheme = _HTTP_SCHEMES.get(parsed.scheme)
    if scheme is None:
        return None
    return urlunsplit((scheme, parsed.netloc, voice_path, "", ""))
