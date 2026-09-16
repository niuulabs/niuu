"""Lock the Python handshake replica to the shipped Swift client, byte for byte.

The Ravn OpenClaw shim has to satisfy a client we cannot rebuild here — there
is no Swift toolchain on Thor. What we *can* do is pin the Python
reimplementation to the exact golden strings the Swift unit tests assert, so a
drift in either side fails here in milliseconds instead of on a TestFlight
build, where the symptom is a hard disconnect with no user-visible error.

Golden strings are copied verbatim from:
  lexi-ios/packages/OpenClawKit/Tests/OpenClawKitTests/OpenClawKitTests.swift:470
  lexi-ios/packages/OpenClawKit/Tests/OpenClawKitTests/ClientInfoTests.swift:65

The device-id rule and the error taxonomy are pinned to what the RUNNING
gateway (2026.6.11) actually did — see fixtures/openclaw_handshake_vector.json.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_ravn.openclaw_handshake import (
    IOS_CLIENT_ID,
    IOS_DEVICE,
    IOS_MODE,
    IOS_PLATFORM,
    MAX_PROTOCOL,
    MIN_PROTOCOL,
    SCOPES,
    FakeDevice,
    build_challenge_payload,
    build_connect_request,
    derive_device_id,
)

FIXTURES = Path(__file__).parent / "fixtures"
VECTOR = FIXTURES / "openclaw_handshake_vector.json"


class TestChallengePayload:
    """The signed payload is wire-locked: one wrong separator = hard disconnect."""

    def test_matches_swift_ios_golden_string(self) -> None:
        payload = build_challenge_payload(
            device_id="DEVID",
            client_id=IOS_CLIENT_ID,
            mode=IOS_MODE,
            scopes=SCOPES,
            signed_at_ms=1700000000000,
            token="TOK",
            nonce="NONCE123",
            platform=IOS_PLATFORM,
            device=IOS_DEVICE,
        )
        assert payload == (
            "v3|DEVID|openclaw-ios|ui|operator|"
            "operator.read,operator.write|1700000000000|TOK|NONCE123|ios|iphone"
        )

    def test_matches_swift_clientinfo_golden_string(self) -> None:
        payload = build_challenge_payload(
            device_id="device123",
            client_id=IOS_CLIENT_ID,
            mode=IOS_MODE,
            scopes=SCOPES,
            signed_at_ms=1700000000000,
            token="tok-abc",
            nonce="nonce-xyz",
            platform=IOS_PLATFORM,
            device=IOS_DEVICE,
        )
        assert payload == (
            "v3|device123|openclaw-ios|ui|operator|"
            "operator.read,operator.write|1700000000000|tok-abc|nonce-xyz|ios|iphone"
        )

    def test_role_slot_is_literal_operator_not_a_scope(self) -> None:
        """Slot 5 is the hardcoded role, which is easy to confuse with scopes."""
        parts = build_challenge_payload(
            device_id="D",
            client_id=IOS_CLIENT_ID,
            mode=IOS_MODE,
            scopes=["a.read"],
            signed_at_ms=1,
            token="T",
            nonce="N",
            platform=IOS_PLATFORM,
            device=IOS_DEVICE,
        ).split("|")
        assert parts[4] == "operator"
        assert parts[5] == "a.read"
        assert len(parts) == 11


class TestDeviceIdentity:
    """deviceId is derived, not chosen — proven by a real gateway rejection."""

    def test_device_id_is_sha256_hex_of_raw_public_key(self) -> None:
        import hashlib

        device = FakeDevice()
        assert device.device_id == hashlib.sha256(device.public_key_raw).hexdigest()
        assert len(device.device_id) == 64

    def test_derive_matches_gateway_rule(self) -> None:
        """Pinned to a fixed vector so a change in the rule is loud.

        infra/device-identity.ts:299-311 — sha256 over the raw key bytes, hex
        digest. An arbitrary id is rejected at connect with
        INVALID_REQUEST / DEVICE_AUTH_DEVICE_ID_MISMATCH (observed, not assumed).
        """
        raw = bytes(range(32))  # 00 01 02 ... 1f
        assert derive_device_id(raw) == (
            "630dcd2966c4336691125448bbb25b4ff412a49c732db2c8abc1b8581bd710dd"
        )

    def test_public_key_is_base64url_unpadded(self) -> None:
        pk = FakeDevice().public_key_b64url
        assert "=" not in pk
        assert "+" not in pk and "/" not in pk

    def test_signature_verifies_against_the_signed_payload(self) -> None:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )

        device = FakeDevice.from_seed(bytes([7]) * 32)
        req = build_connect_request(
            device=device, token="TOK", nonce="N1", signed_at_ms=1700000000000
        )
        params = req["params"]
        expected = build_challenge_payload(
            device_id=device.device_id,
            client_id=IOS_CLIENT_ID,
            mode=IOS_MODE,
            scopes=SCOPES,
            signed_at_ms=1700000000000,
            token="TOK",
            nonce="N1",
            platform=IOS_PLATFORM,
            device=IOS_DEVICE,
        )
        import base64

        def unb64url(s: str) -> bytes:
            return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))

        pub = Ed25519PublicKey.from_public_bytes(unb64url(params["device"]["publicKey"]))
        # Raises on mismatch.
        pub.verify(unb64url(params["device"]["signature"]), expected.encode())


class TestConnectRequest:
    def test_advertises_the_protocol_range_the_client_supports(self) -> None:
        params = build_connect_request(device=FakeDevice(), token="T", nonce="N")["params"]
        assert params["minProtocol"] == MIN_PROTOCOL == 3
        assert params["maxProtocol"] == MAX_PROTOCOL == 4

    def test_push_token_is_nested_under_auth(self) -> None:
        """The shipped app sends auth.pushToken once APNs registration ran.

        Upstream's connect schema is additionalProperties:false, so a shim that
        mirrors that schema too literally would reject a real phone. The shim
        must tolerate this key.
        """
        params = build_connect_request(
            device=FakeDevice(), token="T", nonce="N", push_token="apns-abc"
        )["params"]
        assert params["auth"]["pushToken"] == {"platform": "apns", "token": "apns-abc"}
        assert params["auth"]["token"] == "T"


@pytest.mark.skipif(not VECTOR.exists(), reason="golden vector not captured")
class TestAgainstCapturedGateway:
    """Pin the shim's obligations to what the real gateway actually returned."""

    @staticmethod
    def _hello() -> dict:
        data = json.loads(VECTOR.read_text())
        for frame in data["frames"]:
            f = frame["frame"]
            if f.get("id") == "c1" and f.get("ok"):
                return f["payload"]
        raise AssertionError("no successful connect response in the vector")

    def test_challenge_arrives_first_and_unprompted(self) -> None:
        data = json.loads(VECTOR.read_text())
        first = data["frames"][0]
        assert first["dir"] == "recv"
        assert first["frame"]["event"] == "connect.challenge"
        assert "nonce" in first["frame"]["payload"]

    def test_challenge_carries_no_seq(self) -> None:
        """seq numbering starts after hello-ok, so the challenge has none."""
        data = json.loads(VECTOR.read_text())
        assert data["frames"][0]["frame"].get("seq") is None

    def test_hello_ok_required_fields(self) -> None:
        hello = self._hello()
        assert hello["type"] == "hello-ok"
        assert MIN_PROTOCOL <= hello["protocol"] <= MAX_PROTOCOL
        assert set(hello["server"]) >= {"version", "connId"}
        assert set(hello["auth"]) >= {"role", "scopes", "deviceToken", "issuedAtMs"}
        assert hello["policy"]["tickIntervalMs"] > 0

    def test_running_gateway_negotiates_protocol_4(self) -> None:
        """Recorded because it contradicts the in-repo comment.

        OCConstants says "Thor / Mac Mini speak protocol v3, while Spark speaks
        v4". Thor is on 2026.6.11 and negotiated **4**. The shim may still
        advertise 3 — the client accepts 3...4 and reads only the cumulative
        `message.blocks`, ignoring v4's `deltaText`/`replace` — but nobody
        should plan against "Thor is a v3 gateway".
        """
        hello = self._hello()
        assert hello["protocol"] == 4
        assert hello["server"]["version"] == "2026.6.11"

    def test_tick_cadence_matches_advertised_policy(self) -> None:
        data = json.loads(VECTOR.read_text())
        ticks = [
            f["offset_ms"]
            for f in data["frames"]
            if f["dir"] == "recv" and f["frame"].get("event") == "tick"
        ]
        assert len(ticks) >= 2, "no heartbeat observed"
        gaps = [b - a for a, b in zip(ticks, ticks[1:])]
        advertised = self._hello()["policy"]["tickIntervalMs"]
        for gap in gaps:
            assert abs(gap - advertised) < 2000, f"tick gap {gap}ms vs {advertised}ms"

    def test_events_carry_monotonic_seq(self) -> None:
        data = json.loads(VECTOR.read_text())
        seqs = [
            f["frame"]["seq"]
            for f in data["frames"]
            if f["dir"] == "recv" and f["frame"].get("seq") is not None
        ]
        assert seqs == sorted(seqs)
        assert seqs == list(range(seqs[0], seqs[0] + len(seqs)))

    def test_responses_may_arrive_out_of_order_with_events(self) -> None:
        """A response is NOT necessarily the next frame after its request.

        Observed: a `health` event landed between `agents.list` and its own
        response. Any client — including our own test client — must correlate
        responses by `id` rather than reading the next frame off the socket.
        """
        data = json.loads(VECTOR.read_text())
        order = [
            ("res", f["frame"].get("id")) if "event" not in f["frame"] else ("event", None)
            for f in data["frames"]
            if f["dir"] == "recv"
        ]
        first_event_after_hello = next(
            i for i, (kind, _) in enumerate(order) if kind == "event" and i > 1
        )
        later_responses = [
            i for i, (kind, _) in enumerate(order) if kind == "res" and i > first_event_after_hello
        ]
        assert later_responses, "expected at least one response after an interleaved event"
