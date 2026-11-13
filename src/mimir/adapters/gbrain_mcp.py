"""What every gbrain adapter shares: the MCP transport, reply parsing and sealing.

gbrain answers JSON-RPC ``tools/call`` over HTTP at its ``/mcp`` endpoint with a
bearer token (``gbrain auth create <name>``), either as plain JSON or as one SSE
``data:`` frame. :class:`GBrainMcpClient` sends a call and returns the MCP result
object; the adapters decide what an error means for them.

:class:`Sealer` signs text Mímir stores in gbrain (a fact's provenance, a page's
attribution) with a key only Mímir holds, so a row someone wrote to gbrain
directly cannot claim a trust level or a layer: ``<tag>.<signature>.<json>``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx

#: Yields the bearer token for the next call (a static token, or one a layer client fetches).
BearerProvider = Callable[[], Awaitable[str]]

logger = logging.getLogger(__name__)

#: Bytes of the HMAC-SHA256 digest kept in a seal. 128 bits is a full-strength MAC;
#: shorter keeps the sealed text inside gbrain's 500-character provenance field.
_SEAL_DIGEST_BYTES = 16


class GBrainTransportError(RuntimeError):
    """gbrain could not be reached, or its reply could not be read."""


def read_secret(value: str, path: str, *, what: str) -> str:
    """The secret given inline or in a file; the file wins. Empty when neither is set."""
    if path:
        try:
            return Path(path).expanduser().read_text().strip()
        except OSError as exc:
            raise ValueError(f"cannot read the {what} file {path!r}: {exc}") from exc
    return value.strip()


def parse_mcp_response(raw: str) -> dict[str, Any]:
    """Parse a gbrain MCP reply, which may be JSON or an SSE ``data:`` frame."""
    text = raw.strip()
    if not text:
        raise RuntimeError("gbrain MCP returned an empty response")
    if text.startswith("{"):
        return json.loads(text)
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("data:"):
            body = line[len("data:") :].strip()
            if body:
                return json.loads(body)
    raise RuntimeError(f"gbrain MCP returned an unparseable response: {text[:200]}")


def text_result(result: dict[str, Any]) -> str:
    """Join the text blocks of an MCP tool result."""
    blocks = result.get("content")
    if not isinstance(blocks, list):
        return ""
    return "\n".join(
        str(b.get("text", "")) for b in blocks if isinstance(b, dict) and b.get("type") == "text"
    ).strip()


def result_payload(result: dict[str, Any]) -> Any:
    """The JSON a tool answered with: structured content, else its text block parsed.

    gbrain puts the JSON value first and may follow it with a line for people (an empty
    ``query`` page answers ``[]`` and then ``0 results. retrieved 0 before trimming…``),
    so the leading JSON value is the answer and the note after it is not.

    Returns ``None`` when the result carries neither (or text that does not start with
    JSON).
    """
    structured = result.get("structuredContent")
    if structured is not None:
        return structured
    text = text_result(result)
    if not text:
        return None
    try:
        value, end = json.JSONDecoder().raw_decode(text)
    except json.JSONDecodeError:
        return None
    rest = text[end:]
    if rest and not rest.startswith("\n"):
        # Prose that merely starts like JSON ("3 pages found"), not a JSON answer.
        return None
    return value


class GBrainMcpClient:
    """One gbrain MCP endpoint, called with a bearer token.

    The token is either fixed (*api_token*, a ``gbrain auth create`` token) or fetched
    for each call by *bearer*, which is how a per-layer OAuth client presents a token
    that expires and is renewed. Exactly one of the two is given. Many clients may share one
    *http* connection pool; a shared pool is closed by its owner, not by the client.
    """

    def __init__(
        self,
        mcp_url: str,
        api_token: str = "",
        *,
        timeout_seconds: float,
        bearer: BearerProvider | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        if not mcp_url:
            raise ValueError("a gbrain MCP URL is required")
        if bearer is not None and api_token:
            raise ValueError("give a gbrain client either an API token or a bearer provider")
        if bearer is None and not api_token:
            raise ValueError(
                "a gbrain API token is required; create one with `gbrain auth create <name>`"
            )
        self.url = mcp_url.rstrip("/")
        self._token = api_token
        self._bearer = bearer
        self._timeout = float(timeout_seconds)
        self._client: httpx.AsyncClient | None = http
        self._owns_client = http is None

    def http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    @property
    def token(self) -> str:
        """The fixed API token; empty on a client that fetches its bearer per call."""
        return self._token

    async def close(self) -> None:
        if not self._owns_client:
            return
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke one tool and return its MCP result object (``isError`` included).

        Raises:
            GBrainTransportError: the HTTP call failed, the reply could not be parsed,
                it was a JSON-RPC error, or it carried no result object.
        """
        token = await self._bearer() if self._bearer is not None else self._token
        try:
            response = await self.http().post(
                self.url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                },
            )
            response.raise_for_status()
            message = parse_mcp_response(response.text)
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            raise GBrainTransportError(f"gbrain {name} failed at {self.url}: {exc}") from exc
        if "error" in message:
            raise GBrainTransportError(f"gbrain {name} returned an MCP error: {message['error']}")
        result = message.get("result")
        if not isinstance(result, dict):
            raise GBrainTransportError(f"gbrain {name} returned no result object: {message}")
        return result


class Sealer:
    """Signs and verifies ``<tag>.<signature>.<json>`` texts with Mímir's own key."""

    def __init__(self, key: str) -> None:
        if not key:
            raise ValueError("a signing key is required")
        self._key = key.encode()

    def sign(self, body: str) -> str:
        digest = hmac.new(self._key, body.encode(), hashlib.sha256).digest()[:_SEAL_DIGEST_BYTES]
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")

    def seal(self, tag: str, payload: dict[str, Any]) -> str:
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
        return f"{tag}.{self.sign(body)}.{body}"

    def open(self, tag: str, text: str | None) -> dict[str, Any] | None:
        """The payload of a sealed text, or None when it is not ours or does not verify."""
        if not text or not text.startswith(f"{tag}."):
            return None
        _, _, rest = text.partition(".")
        signature, sep, body = rest.partition(".")
        if not sep or not hmac.compare_digest(signature, self.sign(body)):
            logger.error(
                "gbrain row carries a %s seal whose signature does not verify; "
                "reading it as unattested",
                tag,
            )
            return None
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None
