"""HTTP client for `niuu join`/`niuu leave`/`niuu guild pair` against Guild.

Three distinct credential shapes are in play here, so this module does not
reuse ``cli.api.client.APIClient`` (built around one long-lived bearer
token): pairing-code minting uses the operator's own session token, `join`
uses the one-time pairing code itself as the bearer token, and
heartbeat/leave carry no bearer token at all — they are Ed25519-signed
instead (see ``niuu.adapters.node_signature``).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from cli.auth.node_key import NodeIdentity
from niuu.adapters.node_signature import signing_message

REQUEST_TIMEOUT_SECONDS = 30.0


class GuildAPIError(Exception):
    """Raised when Guild rejects a join/pairing/heartbeat/leave request."""


@dataclass(frozen=True)
class OfferedInstance:
    kind: str
    base_url: str
    ravn_base_url: str = ""
    config: dict[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"kind": self.kind, "baseUrl": self.base_url}
        if self.ravn_base_url:
            payload["ravnBaseUrl"] = self.ravn_base_url
        if self.config:
            payload["config"] = self.config
        return payload


def _raise_for_status(response: httpx.Response) -> None:
    if response.is_success:
        return
    detail = response.text
    try:
        detail = response.json().get("detail", detail)
    except ValueError:
        pass
    raise GuildAPIError(f"Guild returned {response.status_code}: {detail}")


async def mint_pairing_code(
    guild_url: str,
    *,
    access_token: str,
    allow_plaintext: bool = False,
    allow_untrusted_node_auth: bool = False,
) -> dict[str, Any]:
    """POST /guild/pairing-codes as the authenticated operator. Admin/owner only."""
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"{guild_url.rstrip('/')}/api/v1/niuu/guild/pairing-codes",
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "allowPlaintext": allow_plaintext,
                "allowUntrustedNodeAuth": allow_untrusted_node_auth,
            },
        )
    _raise_for_status(response)
    return response.json()


async def join(
    guild_url: str,
    *,
    code: str,
    node_name: str,
    public_key: str,
    node_auth_mode: str,
    instances: list[OfferedInstance],
) -> dict[str, Any]:
    """POST /guild/join, authenticated by the pairing code itself."""
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"{guild_url.rstrip('/')}/api/v1/niuu/guild/join",
            headers={"Authorization": f"Bearer {code}"},
            json={
                "code": code,
                "nodeName": node_name,
                "publicKey": public_key,
                "nodeAuthMode": node_auth_mode,
                "instances": [item.to_payload() for item in instances],
            },
        )
    _raise_for_status(response)
    return response.json()


def _signed_headers(
    identity: NodeIdentity, *, node_id: str, method: str, path: str, body: bytes
) -> dict[str, str]:
    # Milliseconds, not seconds: a heartbeat and a leave issued within the
    # same second must both be able to advance the strictly-increasing
    # replay watermark (niuu.adapters.node_signature).
    timestamp = int(time.time() * 1000)
    signature = identity.sign(signing_message(method, path, timestamp, body))
    return {
        "x-niuu-node-id": node_id,
        "x-niuu-timestamp": str(timestamp),
        "x-niuu-signature": signature,
    }


async def heartbeat(
    guild_url: str,
    *,
    node_id: str,
    identity: NodeIdentity,
    instances: list[OfferedInstance],
) -> dict[str, Any]:
    """POST /guild/nodes/{node_id}/heartbeat, Ed25519-signed."""
    path = f"/api/v1/niuu/guild/nodes/{node_id}/heartbeat"
    body = json.dumps({"instances": [item.to_payload() for item in instances]}).encode("utf-8")
    headers = _signed_headers(identity, node_id=node_id, method="POST", path=path, body=body)
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"{guild_url.rstrip('/')}{path}",
            headers={**headers, "content-type": "application/json"},
            content=body,
        )
    _raise_for_status(response)
    return response.json()


async def leave(guild_url: str, *, node_id: str, identity: NodeIdentity) -> None:
    """POST /guild/nodes/{node_id}/leave, Ed25519-signed."""
    path = f"/api/v1/niuu/guild/nodes/{node_id}/leave"
    headers = _signed_headers(identity, node_id=node_id, method="POST", path=path, body=b"")
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        response = await client.post(f"{guild_url.rstrip('/')}{path}", headers=headers)
    _raise_for_status(response)
