"""Who sent a user-role turn: the server-set sender label (LEX-1910).

Every message posted into a session records its sender, set by the server from the
path the message took, never parsed from its text:

* ``{"kind": "user"}`` — the session owner typing or dictating in a Lexi client;
* ``{"kind": "agent", "sub_kind": "session", "name", "id", "host"}`` — another
  session, through Forge MCP ``send_message`` or a Guild relay (``via: "guild"``);
* ``{"kind": "agent", "sub_kind": "voice_call", "id": <call id>}`` — the owner speaking
  in a Lexi voice call, relayed by the voice agent (LEX-2013); ``id`` when the caller
  names the call;
* ``{"kind": "agent", "sub_kind": "automation", "name": <driver>}`` — a script,
  driver, crash reporter or scheduled summary;
* ``{"kind": "agent", "sub_kind": "unknown"}`` — a message that named no sender and
  carries neither a client request id nor a ``voice-call-`` one;
* ``assistant`` — the session's own model; its turns carry no sender, the role
  says it, and no message may claim it.

Clients read the label first. The receiving model cannot see it, so for every
sender other than the user the text it receives starts with one header line.
"""

from __future__ import annotations

import re
from typing import Any

SENDER_KEY = "sender"
# The Guild gateway names its own node on a message it hands the in-process Forge,
# so a session's sender carries the host the way a relayed one does.
SENDER_NODE_HEADER = "x-niuu-sender-node"

KIND_USER = "user"
KIND_AGENT = "agent"
KIND_ASSISTANT = "assistant"

SUB_KIND_SESSION = "session"
SUB_KIND_VOICE_CALL = "voice_call"
SUB_KIND_AUTOMATION = "automation"
SUB_KIND_UNKNOWN = "unknown"

VIA_GUILD = "guild"

_AGENT_SUB_KINDS = frozenset(
    {SUB_KIND_SESSION, SUB_KIND_VOICE_CALL, SUB_KIND_AUTOMATION, SUB_KIND_UNKNOWN}
)
_IDENTITY_FIELDS = ("name", "id", "host")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")

# Request ids the Lexi clients mint for the owner's own messages, until every client
# sends ``sender`` itself: the iOS app's UPPERCASE UUIDs, the web and Mac clients'
# ``<epoch ms>-<base36>`` ids, and the ``forge-ios-``/``forge-web-``/``forge-mac-`` ids.
_CLIENT_REQUEST_ID = re.compile(
    r"^(?:[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}"
    r"|\d{13}-[0-9a-z]+"
    r"|forge-(?:ios|web|mac)-.+)$"
)
# The request id the Lexi voice agent mints for each message it relays from a call
# (``voice-call-<uuid>``, one per message, so it is not the call id) (LEX-2013).
_VOICE_CALL_REQUEST_ID = re.compile(r"^voice-call-[A-Za-z0-9._:-]+$")


# A one-line slash command (``/clear``, ``/effort high``), not prose that starts with
# a path such as ``/tmp/app.py``: the harness runs it, the model never reads it.
_SLASH_COMMAND = re.compile(r"^\s*/[A-Za-z][\w:-]*(?:[ \t][^\n]*)?\s*$")


class SenderError(ValueError):
    """A ``sender`` value this server cannot record."""


def user_sender() -> dict[str, Any]:
    return {"kind": KIND_USER}


def unknown_sender() -> dict[str, Any]:
    return {"kind": KIND_AGENT, "sub_kind": SUB_KIND_UNKNOWN}


def session_sender(
    *, session_id: str, name: str, host: str, relayed: bool = False
) -> dict[str, Any]:
    """Another session, identified by the server from its credential or relay."""
    sender: dict[str, Any] = {
        "kind": KIND_AGENT,
        "sub_kind": SUB_KIND_SESSION,
        "id": session_id,
    }
    if name:
        sender["name"] = name
    if host:
        sender["host"] = host
    if relayed:
        sender["via"] = VIA_GUILD
    return _checked(sender)


def parse_claimed_sender(value: Any) -> dict[str, Any]:
    """Validate the ``sender`` a REST caller names for its own message.

    Accepts ``"user"``, ``"voice_call"``, ``"automation"`` or ``"unknown"``, or an
    object ``{"kind", "sub_kind", "name", "id"}``. A session sender is refused: it
    comes only from ``send_message``, whose credential says which session is asking.
    """
    sender = _parse(value)
    if sender["kind"] == KIND_ASSISTANT:
        raise SenderError(
            "sender 'assistant' is the session's own model; a message cannot claim it"
        )
    if sender.get("sub_kind") == SUB_KIND_SESSION:
        raise SenderError(
            "sender 'session' is set only by Forge MCP send_message, from the calling "
            "session's credential; a script uses 'automation'"
        )
    return sender


def parse_recorded_sender(value: Any) -> dict[str, Any] | None:
    """Read a sender already set by the server (a broker frame or a stored turn).

    ``None`` when absent. A malformed value raises: the server wrote it.
    """
    if value is None:
        return None
    return _parse(value)


def voice_call_sender() -> dict[str, Any]:
    return {"kind": KIND_AGENT, "sub_kind": SUB_KIND_VOICE_CALL}


def sender_for_request_id(request_id: str | None) -> dict[str, Any]:
    """The sender of a message that named none: the owner's client, a voice call, or unknown.

    Transition rule until every Lexi client and the voice agent send ``sender``: the
    request id shapes the clients mint are the owner's, a ``voice-call-`` id is a voice
    call (LEX-2013), and anything else is an unknown sender, never the user.
    """
    if not request_id:
        return unknown_sender()
    if _CLIENT_REQUEST_ID.match(request_id):
        return user_sender()
    if _VOICE_CALL_REQUEST_ID.match(request_id):
        return voice_call_sender()
    return unknown_sender()


def sender_header(sender: dict[str, Any]) -> str | None:
    """The one line the receiving model sees before a non-user message, else ``None``."""
    if sender["kind"] != KIND_AGENT:
        return None
    sub_kind = sender["sub_kind"]
    if sub_kind == SUB_KIND_SESSION:
        name = sender.get("name")
        who = f"{name} ({sender['id']})" if name else sender["id"]
        where = f" on {sender['host']}" if sender.get("host") else ""
        relayed = ", relayed through the Guild" if sender.get("via") == VIA_GUILD else ""
        return f"[Message from session {who}{where}{relayed}]"
    if sub_kind == SUB_KIND_VOICE_CALL:
        # The owner, speaking: a voice call reaches a session only through the
        # platform's voice agent (LEX-2013), so this is the user, not a third party.
        call = f" {sender['id']}" if sender.get("id") else ""
        return f"[From the user by voice: Lexi voice call{call}, relayed by the voice agent]"
    if sub_kind == SUB_KIND_AUTOMATION:
        name = f" {sender['name']}" if sender.get("name") else ""
        return f"[Message from automation{name}, not typed by the user]"
    return "[Message from an unidentified sender through the Forge API, not typed by the user]"


def is_owner_by_voice(sender: dict[str, Any]) -> bool:
    """A voice call: the owner speaking, relayed by the platform's voice agent (LEX-2013)."""
    return sender["kind"] == KIND_AGENT and sender["sub_kind"] == SUB_KIND_VOICE_CALL


def text_for_model(sender: dict[str, Any], content: str) -> str:
    """What the receiving model gets: the header line, a blank line, the message.

    A one-line slash command goes through unchanged, so another session can still
    send ``/clear``; its sender is recorded on the turn all the same. Anything
    else starting with ``/`` is prose and is headed.
    """
    header = sender_header(sender)
    if header is None or _SLASH_COMMAND.match(content):
        return content
    return f"{header}\n\n{content}"


def _parse(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return _from_word(value)
    if not isinstance(value, dict):
        raise SenderError("sender must be a string or an object with a 'kind'")
    kind = value.get("kind")
    if kind in (KIND_USER, KIND_ASSISTANT):
        return {"kind": kind}
    if kind != KIND_AGENT:
        raise SenderError(f"sender kind must be user, agent or assistant, not {kind!r}")
    sub_kind = value.get("sub_kind")
    if sub_kind not in _AGENT_SUB_KINDS:
        raise SenderError(
            f"agent sender sub_kind must be one of {sorted(_AGENT_SUB_KINDS)}, not {sub_kind!r}"
        )
    sender: dict[str, Any] = {"kind": KIND_AGENT, "sub_kind": sub_kind}
    for field in _IDENTITY_FIELDS:
        if value.get(field) is not None:
            sender[field] = value[field]
    if value.get("via") is not None:
        if value["via"] != VIA_GUILD:
            raise SenderError(f"sender via must be {VIA_GUILD!r}, not {value['via']!r}")
        sender["via"] = VIA_GUILD
    if sub_kind == SUB_KIND_SESSION and not sender.get("id"):
        raise SenderError("a session sender needs its session id")
    return _checked(sender)


def _from_word(word: str) -> dict[str, Any]:
    if word in (KIND_USER, KIND_ASSISTANT):
        return {"kind": word}
    if word == SUB_KIND_SESSION:
        raise SenderError("a session sender needs its session id; send an object")
    if word in _AGENT_SUB_KINDS:
        return {"kind": KIND_AGENT, "sub_kind": word}
    raise SenderError(
        "sender must be one of user, voice_call, automation or unknown, "
        f"or an object with a 'kind'; got {word!r}"
    )


def _checked(sender: dict[str, Any]) -> dict[str, Any]:
    for field in _IDENTITY_FIELDS:
        value = sender.get(field)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            raise SenderError(f"sender {field} must be a non-empty string")
        # The header is one line the model trusts; a newline would let a sender
        # write a second, forged one.
        if _CONTROL_CHARACTERS.search(value):
            raise SenderError(f"sender {field} must not contain control characters")
    return sender
