"""OpenClaw gateway wire protocol — the half Ravn must speak to be a gateway.

This is the frame layer for :mod:`ravn.adapters.channels.gateway_openclaw`. It
exists so that the shipped LexiChat iOS client — which we cannot rebuild and do
not control — can connect to Ravn believing it is talking to an OpenClaw
gateway.

Everything here is pinned to observation, not documentation. The golden vector
in ``tests/test_ravn/fixtures/openclaw_handshake_vector.json`` was captured from
the gateway actually running on this box (``2026.6.11``), because three sources
disagreed: the upstream docs, the local ``thirdparty/openclaw`` checkout
(``2026.5.30``), and the live process. Only the live one matters — it is the one
the phone talks to.

Failure modes this module is shaped around
------------------------------------------
Each of these presents as a hang or a silent dead connection rather than an
error, which is why they are encoded here rather than left to the caller:

* **No challenge = permanent wedge.** The client has no challenge timeout. A
  socket that upgrades and never pushes ``connect.challenge`` leaves it in
  ``.waitingForChallenge`` forever with no reconnect. The challenge must be
  pushed unprompted and first.
* **Out-of-range protocol = silent hard disconnect**, with no reconnect
  scheduled and no user-visible error — the worst debugging experience
  available. We advertise 3, inside the client's accepted ``3...4``.
* **A shorthand error frame is undecodable.** Swift's ``WSError`` has
  non-optional ``code`` and ``message``, so ``{"ok":false,"error":"oops"}``
  never decodes, never reaches the response handler, and hangs the caller for
  its full 15 s RPC timeout. :func:`error_frame` always emits the full shape.
* **A missed tick kills the transport.** The client tears it down at 2.5x the
  advertised ``tickIntervalMs``, and *only* ``hello-ok`` and ``event:"tick"``
  reset that watchdog — chat and agent events do not. So ticks must continue
  during a long turn.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# --- protocol constants -----------------------------------------------------

#: Wire protocol we advertise. The client accepts 3...4 and reads only the
#: cumulative ``message.blocks``; it handles neither v4's ``deltaText`` nor
#: ``replace`` (grep for deltaText across the Swift tree returns nothing). We
#: therefore advertise the lower version we fully implement. Note that the real
#: Thor gateway negotiates 4 — the in-repo comment claiming Thor is v3 is stale.
PROTOCOL_VERSION = 3

#: Observed on the live gateway. 25 MB, not the 16 MB the design assumed.
MAX_PAYLOAD_BYTES = 26_214_400
MAX_BUFFERED_BYTES = 52_428_800

#: Advertised heartbeat period. We emit at half this (see TICK_EMIT_SECONDS) so
#: a single dropped tick cannot trip the client's 2.5x watchdog.
TICK_INTERVAL_MS = 30_000
TICK_EMIT_SECONDS = 15.0

#: The gateway rejects a signature older than this. Mirrors upstream's
#: DEVICE_SIGNATURE_SKEW_MS (message-handler.ts:167).
DEVICE_SIGNATURE_SKEW_MS = 2 * 60 * 1000

SCOPES = ("operator.read", "operator.write")


class ErrorCodes:
    """The subset of upstream's taxonomy this shim emits."""

    UNAUTHORIZED = "UNAUTHORIZED"
    INVALID_REQUEST = "INVALID_REQUEST"
    NOT_FOUND = "NOT_FOUND"
    UNSUPPORTED = "UNSUPPORTED"
    INTERNAL = "INTERNAL"
    RATE_LIMITED = "RATE_LIMITED"


# --- base64url --------------------------------------------------------------


def b64url_decode(value: str) -> bytes:
    """Decode unpadded base64url, as Swift's Data.base64URLEncodedString emits."""
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


# --- frames -----------------------------------------------------------------


def response_frame(req_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"type": "res", "id": req_id, "ok": True, "payload": payload or {}}


def error_frame(
    req_id: str,
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """An error the shipped client can actually decode.

    ``code`` and ``message`` are non-optional in Swift's ``WSError``. Omitting
    either makes the whole response undecodable, so it is dropped before it
    reaches the response handler and the caller hangs 15 s instead of erroring.
    """
    body: dict[str, Any] = {"code": code, "message": message}
    body["details"] = {"code": code, **(details or {})}
    return {"type": "res", "id": req_id, "ok": False, "error": body}


def event_frame(event: str, payload: dict[str, Any], seq: int | None = None) -> dict[str, Any]:
    frame: dict[str, Any] = {"type": "event", "event": event, "payload": payload}
    if seq is not None:
        frame["seq"] = seq
    return frame


def challenge_frame(nonce: str) -> dict[str, Any]:
    """The unprompted first frame.

    Carries no ``seq``: numbering starts after ``hello-ok`` (observed — the
    captured challenge has no seq field, and the first event after hello-ok
    is seq 1).
    """
    return {
        "type": "event",
        "event": "connect.challenge",
        "payload": {"nonce": nonce, "ts": int(time.time() * 1000)},
    }


def new_nonce() -> str:
    return secrets.token_urlsafe(24)


def hello_ok_payload(
    *,
    server_version: str,
    conn_id: str,
    device_token: str,
    agent_id: str,
    main_session_key: str,
    methods: list[str],
) -> dict[str, Any]:
    """The connect response.

    Only ``type`` and ``protocol`` are load-bearing for the client — every other
    field of Swift's ``HelloOKPayload`` is Optional — but the real gateway sends
    server/policy/auth/snapshot and some are read opportunistically, so we send
    a faithful subset rather than the bare minimum.
    """
    return {
        "type": "hello-ok",
        "protocol": PROTOCOL_VERSION,
        "server": {"version": server_version, "connId": conn_id},
        "policy": {
            "maxPayload": MAX_PAYLOAD_BYTES,
            "maxBufferedBytes": MAX_BUFFERED_BYTES,
            "tickIntervalMs": TICK_INTERVAL_MS,
        },
        "auth": {
            "role": "operator",
            "scopes": list(SCOPES),
            "deviceToken": device_token,
            "issuedAtMs": int(time.time() * 1000),
        },
        "features": {"methods": methods, "events": ["chat", "tick", "session.updated"]},
        "snapshot": {
            "sessionDefaults": {
                "defaultAgentId": agent_id,
                "mainKey": main_session_key,
                "mainSessionKey": main_session_key,
            }
        },
    }


# --- device auth ------------------------------------------------------------


def derive_device_id(public_key_raw: bytes) -> str:
    """``sha256(rawPublicKey).hex()`` — upstream's rule, reproduced.

    Observed the hard way: a probe presenting an arbitrary device id was
    rejected with ``INVALID_REQUEST / DEVICE_AUTH_DEVICE_ID_MISMATCH``.

    The consequence is a good one — device ids are *self-certifying*. A client
    cannot claim an id it does not hold the private key for, so trust-on-first-
    use needs no out-of-band pairing step, provided we actually perform this
    check. Skipping it would let anyone impersonate a known device id.
    """
    return hashlib.sha256(public_key_raw).hexdigest()


def build_challenge_payload(
    *,
    device_id: str,
    client_id: str,
    mode: str,
    scopes: list[str],
    signed_at_ms: int,
    token: str,
    nonce: str,
    platform: str,
    device: str,
) -> str:
    """The v3 signed payload. Field order is wire-locked — do not reorder.

    ``v3|deviceId|clientId|mode|operator|scopes(comma)|signedAt|token|nonce|platform|device``

    Slot 5 is the literal role, not a scope. Mirrors
    ``GatewayWebSocketClient.buildChallengePayload`` (Swift), which carries its
    own "do not reorder" comment.
    """
    return "|".join(
        [
            "v3",
            device_id,
            client_id,
            mode,
            "operator",
            ",".join(scopes),
            str(signed_at_ms),
            token,
            nonce,
            platform,
            device,
        ]
    )


class DeviceAuthError(Exception):
    """Device authentication failed. ``reason`` mirrors upstream's vocabulary."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


def verify_device_challenge(
    *,
    device: dict[str, Any],
    client: dict[str, Any],
    scopes: list[str],
    token: str,
    expected_nonce: str,
    now_ms: int | None = None,
) -> str:
    """Validate a ``connect`` device block. Returns the verified device id.

    Performs upstream's checks in upstream's order, so a client sees the same
    rejection reason it would from a real gateway:

    1. device id matches ``sha256(publicKey)``
    2. ``signedAt`` is within +/-2 minutes
    3. nonce present and equal to the one we issued
    4. Ed25519 signature verifies over the v3 payload

    Raises :class:`DeviceAuthError`; never returns falsy.
    """
    now = now_ms if now_ms is not None else int(time.time() * 1000)

    device_id = device.get("id")
    public_key = device.get("publicKey")
    if not isinstance(device_id, str) or not isinstance(public_key, str):
        raise DeviceAuthError("device-identity-required", "device identity required")

    try:
        public_raw = b64url_decode(public_key)
    except Exception as exc:  # malformed base64
        raise DeviceAuthError("device-id-mismatch", "device identity mismatch") from exc
    if not public_raw or derive_device_id(public_raw) != device_id:
        raise DeviceAuthError("device-id-mismatch", "device identity mismatch")

    signed_at = device.get("signedAt")
    if not isinstance(signed_at, int) or abs(now - signed_at) > DEVICE_SIGNATURE_SKEW_MS:
        raise DeviceAuthError("device-signature-stale", "device signature expired")

    nonce = device.get("nonce")
    nonce = nonce.strip() if isinstance(nonce, str) else ""
    if not nonce:
        raise DeviceAuthError("device-nonce-missing", "device nonce required")
    if not secrets.compare_digest(nonce, expected_nonce):
        raise DeviceAuthError("device-nonce-mismatch", "device nonce mismatch")

    signature = device.get("signature")
    if not isinstance(signature, str):
        raise DeviceAuthError("device-signature", "device signature invalid")

    payload = build_challenge_payload(
        device_id=device_id,
        client_id=str(client.get("id", "")),
        mode=str(client.get("mode", "ui")),
        scopes=scopes,
        signed_at_ms=signed_at,
        token=token,
        nonce=nonce,
        platform=str(client.get("platform", "")),
        device=_device_slug(client),
    )
    try:
        Ed25519PublicKey.from_public_bytes(public_raw).verify(
            b64url_decode(signature), payload.encode("utf-8")
        )
    except (InvalidSignature, ValueError) as exc:
        raise DeviceAuthError("device-signature", "device signature invalid") from exc

    return device_id


def _device_slug(client: dict[str, Any]) -> str:
    """Slot 11 of the signed payload — ``ClientInfo.device``, not deviceFamily.

    The client dict sent on the wire carries ``deviceFamily`` ("iPhone") but the
    signature uses ``device`` ("iphone"), which is *not* transmitted. It is
    derived from the platform, so we reconstruct it the same way ClientInfo does
    for its two shipped identities.
    """
    platform = str(client.get("platform", "")).lower()
    if platform == "ios":
        return "iphone"
    if platform == "macos":
        return "mac"
    # Unknown clients: fall back to a lowercased deviceFamily, which is what a
    # third client would most plausibly have signed.
    return str(client.get("deviceFamily", platform)).lower()


def mint_device_token(device_id: str, secret: str) -> str:
    """A deterministic, non-guessable token echoed back in ``hello-ok``.

    The client stores it and may present it on reconnect. Deriving it rather
    than storing random state means it survives a shim restart, which matters
    because Ravn itself has no persistence.
    """
    digest = hashlib.sha256(f"{secret}:{device_id}".encode()).digest()
    return b64url_encode(digest)
