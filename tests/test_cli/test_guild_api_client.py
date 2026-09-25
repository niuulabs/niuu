"""Tests for cli.api.guild — the Guild HTTP client, including a real
cross-check that a CLI-signed heartbeat verifies against the server-side
Ed25519NodeVerifier (catching any drift in the shared signing_message)."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from cli.api.guild import GuildAPIError, OfferedInstance, heartbeat, join, mint_pairing_code
from cli.auth.node_key import NodeIdentity
from niuu.adapters.node_signature import Ed25519NodeVerifier
from niuu.domain.models import RegisteredNode


class _FakeNodeRepository:
    def __init__(self, node: RegisteredNode) -> None:
        self._node = node
        self.recorded: list[int] = []

    async def get(self, node_id: str) -> RegisteredNode | None:
        return self._node if node_id == self._node.id else None

    async def try_advance_watermark(self, node_id: str, *, timestamp_ms: int) -> bool:
        self.recorded.append(timestamp_ms)
        return True


@pytest.mark.asyncio
async def test_mint_pairing_code_sends_the_operators_bearer_token() -> None:
    with respx.mock(base_url="https://guild.example.com") as mock:
        route = mock.post("/api/v1/niuu/guild/pairing-codes").mock(
            return_value=httpx.Response(
                201, json={"code": "c", "expiresAt": "2024-01-01T00:00:00Z"}
            )
        )
        result = await mint_pairing_code("https://guild.example.com", access_token="operator-token")

    assert result["code"] == "c"
    assert route.calls[0].request.headers["authorization"] == "Bearer operator-token"


@pytest.mark.asyncio
async def test_mint_pairing_code_raises_on_error_response() -> None:
    with respx.mock(base_url="https://guild.example.com") as mock:
        mock.post("/api/v1/niuu/guild/pairing-codes").mock(
            return_value=httpx.Response(403, json={"detail": "not an admin"})
        )
        with pytest.raises(GuildAPIError, match="not an admin"):
            await mint_pairing_code("https://guild.example.com", access_token="x")


@pytest.mark.asyncio
async def test_join_authenticates_with_the_pairing_code_as_bearer_token() -> None:
    with respx.mock(base_url="https://guild.example.com") as mock:
        route = mock.post("/api/v1/niuu/guild/join").mock(
            return_value=httpx.Response(
                201,
                json={
                    "nodeId": "node-1",
                    "instances": [],
                    "identity": {"mode": "none", "issuers": []},
                },
            )
        )
        result = await join(
            "https://guild.example.com",
            code="the-code",
            node_name="spark-1",
            public_key="pubkey",
            node_auth_mode="oidc",
            instances=[OfferedInstance(kind="volundr", base_url="http://127.0.0.1:8080")],
        )

    assert result["nodeId"] == "node-1"
    sent = route.calls[0].request
    assert sent.headers["authorization"] == "Bearer the-code"
    import json

    payload = json.loads(sent.content)
    assert payload["instances"] == [{"kind": "volundr", "baseUrl": "http://127.0.0.1:8080"}]
    assert payload["nodeAuthMode"] == "oidc"


@pytest.mark.asyncio
async def test_heartbeat_signature_verifies_against_the_real_server_side_verifier(
    tmp_path,
) -> None:
    """End-to-end proof that the CLI and server agree on what gets signed."""
    identity = NodeIdentity.load_or_create(tmp_path / "node_key")
    node = RegisteredNode(
        id="node-1",
        name="spark-1",
        public_key=identity.public_key_b64,
        tenant_id="tenant-a",
        created_by="admin-1",
        created_at=datetime.now(UTC),
    )
    repo = _FakeNodeRepository(node)
    verifier = Ed25519NodeVerifier(repo, clock_skew_seconds=30.0)

    captured: dict = {}

    async def _capture_and_verify(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["body"] = request.content
        await verifier.verify(
            node_id=request.headers["x-niuu-node-id"],
            method=request.method,
            path=request.url.path,
            timestamp=int(request.headers["x-niuu-timestamp"]),
            body=request.content,
            signature=request.headers["x-niuu-signature"],
        )
        return httpx.Response(200, json={"ok": True, "lastSeenAt": None, "instances": []})

    with respx.mock(base_url="https://guild.example.com") as mock:
        mock.post("/api/v1/niuu/guild/nodes/node-1/heartbeat").mock(side_effect=_capture_and_verify)
        result = await heartbeat(
            "https://guild.example.com",
            node_id="node-1",
            identity=identity,
            instances=[OfferedInstance(kind="volundr", base_url="http://127.0.0.1:8080")],
        )

    assert result["ok"] is True
    assert repo.recorded  # try_advance_watermark was called: signature verified


@pytest.mark.asyncio
async def test_guild_api_error_carries_the_status_code() -> None:
    with respx.mock(base_url="https://guild.example.com") as mock:
        mock.post("/api/v1/niuu/guild/pairing-codes").mock(
            return_value=httpx.Response(401, json={"detail": "revoked"})
        )
        try:
            await mint_pairing_code("https://guild.example.com", access_token="x")
        except GuildAPIError as exc:
            assert exc.status_code == 401
        else:
            pytest.fail("expected GuildAPIError")
