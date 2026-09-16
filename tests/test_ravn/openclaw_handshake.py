"""A faithful Python reimplementation of the LexiChat OpenClaw handshake.

This module is the executable specification of what the shipped iOS client
sends, and therefore of what the Ravn OpenClaw shim must accept. Every constant
here was read out of the Swift source rather than inferred:

* payload format  — ``GatewayWebSocketClient.buildChallengePayload`` (:339-360),
  which carries the comment "Field order and contents are wire-protocol-locked
  — do not reorder".
* client identity — ``ClientInfo.iOS`` (:46-51), "byte-identical to the
  handshake the iOS app has always produced".
* protocol range  — ``OCConstants.min/maxProtocolVersion`` = 3 / 4.
* key + signature — ``DeviceIdentity`` (:10-21): Ed25519, raw 32-byte public
  key, base64**url** for both the public key and the signature.
* connect params  — ``handleChallenge`` (:283-311).

It is used two ways:

1. To probe the *real* OpenClaw gateway and capture a golden ``hello-ok``
   (``capture_openclaw_vector.py``), so the shim is written against observed
   bytes rather than documentation.
2. As the client half of the shim's own wire tests, so a regression in the
   shim's handshake fails in 0.3 s on Thor instead of on a TestFlight build.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

# ClientInfo.iOS — protocol-locked. Changing any of these changes the signed
# payload and the gateway will reject the signature.
IOS_CLIENT_ID = "openclaw-ios"
IOS_PLATFORM = "ios"
IOS_DEVICE = "iphone"
IOS_DEVICE_FAMILY = "iPhone"
IOS_MODE = "ui"
IOS_VERSION = "1.0.0"
IOS_USER_AGENT = "LexiChat/1.0.0"

MIN_PROTOCOL = 3
MAX_PROTOCOL = 4
SCOPES = ["operator.read", "operator.write"]


def b64url(raw: bytes) -> str:
    """Base64url with padding stripped — matches Data.base64URLEncodedString()."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


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
    """Reproduce ``buildChallengePayload`` exactly.

    v3|deviceId|clientId|mode|operator|scopes(comma)|signedAtMs|token|nonce|platform|device

    The literal ``"operator"`` in slot 5 is the *role*, which the Swift client
    hardcodes; it is not the scopes field.
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


def derive_device_id(public_key_raw: bytes) -> str:
    """``sha256(rawPublicKey).hex()`` — the gateway's own derivation.

    Observed, not assumed: an arbitrary device id is rejected at
    ``connect`` with ``INVALID_REQUEST / DEVICE_AUTH_DEVICE_ID_MISMATCH``
    (upstream ``ws-connection/message-handler.ts:929`` compares
    ``deriveDeviceIdFromPublicKey(device.publicKey)`` against ``device.id``;
    the derivation itself is ``infra/device-identity.ts:299-311``).

    This makes device ids self-certifying: a device cannot claim an id it does
    not hold the key for, so the shim can trust-on-first-use safely as long as
    it performs this same check.
    """
    import hashlib

    return hashlib.sha256(public_key_raw).hexdigest()


@dataclass
class FakeDevice:
    """An Ed25519 device identity, equivalent to iOS's Keychain-backed one.

    ``device_id`` is *derived*, never chosen — see :func:`derive_device_id`.
    """

    _key: Ed25519PrivateKey = field(default_factory=Ed25519PrivateKey.generate)

    @classmethod
    def from_seed(cls, seed: bytes) -> FakeDevice:
        """Deterministic identity — lets a test assert a fixed signature."""
        return cls(_key=Ed25519PrivateKey.from_private_bytes(seed))

    @property
    def public_key_raw(self) -> bytes:
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            PublicFormat,
        )

        return self._key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    @property
    def public_key_b64url(self) -> str:
        return b64url(self.public_key_raw)

    @property
    def device_id(self) -> str:
        return derive_device_id(self.public_key_raw)

    def sign(self, payload: str) -> str:
        return b64url(self._key.sign(payload.encode("utf-8")))


def build_connect_request(
    *,
    device: FakeDevice,
    token: str,
    nonce: str,
    req_id: str = "c1",
    signed_at_ms: int | None = None,
    push_token: str | None = None,
) -> dict[str, Any]:
    """Build the exact ``connect`` req frame the iOS client sends."""
    signed_at = signed_at_ms if signed_at_ms is not None else int(time.time() * 1000)
    payload = build_challenge_payload(
        device_id=device.device_id,
        client_id=IOS_CLIENT_ID,
        mode=IOS_MODE,
        scopes=SCOPES,
        signed_at_ms=signed_at,
        token=token,
        nonce=nonce,
        platform=IOS_PLATFORM,
        device=IOS_DEVICE,
    )
    auth: dict[str, Any] = {"token": token}
    if push_token is not None:
        # The shipped app sends this whenever APNs registration has happened.
        # Upstream's connect schema is additionalProperties:false, so a shim
        # that mirrors that schema too strictly would reject a real phone.
        auth["pushToken"] = {"platform": "apns", "token": push_token}

    return {
        "type": "req",
        "id": req_id,
        "method": "connect",
        "params": {
            "minProtocol": MIN_PROTOCOL,
            "maxProtocol": MAX_PROTOCOL,
            "client": {
                "id": IOS_CLIENT_ID,
                "version": IOS_VERSION,
                "platform": IOS_PLATFORM,
                "deviceFamily": IOS_DEVICE_FAMILY,
                "mode": IOS_MODE,
            },
            "role": "operator",
            "scopes": SCOPES,
            "caps": [],
            "commands": [],
            "permissions": {},
            "auth": auth,
            "locale": "en-US",
            "userAgent": IOS_USER_AGENT,
            "device": {
                "id": device.device_id,
                "publicKey": device.public_key_b64url,
                "signature": device.sign(payload),
                "signedAt": signed_at,
                "nonce": nonce,
            },
        },
    }


def build_req(method: str, params: dict[str, Any], req_id: str) -> str:
    return json.dumps({"type": "req", "id": req_id, "method": method, "params": params})
