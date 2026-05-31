"""MCP authentication — token acquisition, storage, and session caching.

Supports three auth patterns:

* ``api_key``          — read from env var (or Bifrost-injected env), inject as
                         HTTP ``Authorization`` header.
* ``client_credentials`` — OAuth 2.0 machine-to-machine; fetches a token from
                         the token endpoint automatically.
* ``device_flow``      — OAuth 2.0 device-authorization grant; Ravn prints the
                         user code + verification URI so the user can approve in
                         a browser, then polls until approved.

Token storage backends
----------------------
* ``LocalEncryptedTokenStore`` — encrypts tokens at rest using Fernet symmetric
  encryption (``cryptography`` package, optional extra).  Falls back to plain
  JSON if the package is unavailable.  Used in Pi mode.
* ``OpenBaoTokenStore`` — reads/writes tokens to an OpenBao (Vault-compatible)
  KV v2 secret.  Used in infra mode.

``MCPAuthSession`` is a per-agent-session cache that wraps either backend.  It
deduplicates token refreshes and provides ``get_auth_headers()`` for transport
injection.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (no magic numbers in business logic — see config for defaults)
# ---------------------------------------------------------------------------

_DEVICE_POLL_INTERVAL_SECONDS = 5.0
_DEVICE_POLL_MAX_ATTEMPTS = 60  # 5 min total
_TOKEN_EXPIRY_BUFFER_SECONDS = 30.0  # refresh this early before real expiry


# ---------------------------------------------------------------------------
# Auth type
# ---------------------------------------------------------------------------


class MCPAuthType(StrEnum):
    """Supported authentication patterns for MCP servers."""

    API_KEY = "api_key"
    DEVICE_FLOW = "device_flow"
    CLIENT_CREDENTIALS = "client_credentials"


# ---------------------------------------------------------------------------
# Token model
# ---------------------------------------------------------------------------


@dataclass
class MCPToken:
    """An OAuth access token (or API key) for a single MCP server."""

    access_token: str
    token_type: str = "Bearer"
    expires_at: float | None = None  # Unix timestamp; None = does not expire

    def is_expired(self) -> bool:
        """Return True if the token has expired (with a safety buffer)."""
        if self.expires_at is None:
            return False
        return time.time() >= (self.expires_at - _TOKEN_EXPIRY_BUFFER_SECONDS)

    def auth_header_value(self) -> str:
        return f"{self.token_type} {self.access_token}"

    def as_auth_headers(self) -> dict[str, str]:
        return {"Authorization": self.auth_header_value()}

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "token_type": self.token_type,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MCPToken:
        return cls(
            access_token=data["access_token"],
            token_type=data.get("token_type", "Bearer"),
            expires_at=data.get("expires_at"),
        )


# ---------------------------------------------------------------------------
# Token store protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class MCPTokenStore(Protocol):
    """Persistence interface for MCP server tokens."""

    async def load(self, server_name: str) -> MCPToken | None:
        """Return the stored token for *server_name*, or None if absent."""
        raise NotImplementedError

    async def save(self, server_name: str, token: MCPToken) -> None:
        """Persist *token* for *server_name*."""
        raise NotImplementedError

    async def delete(self, server_name: str) -> None:
        """Remove the stored token for *server_name* if it exists."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Local encrypted token store (Pi mode)
# ---------------------------------------------------------------------------


def _try_import_fernet() -> Any:
    """Import Fernet from cryptography, return None if unavailable."""
    try:
        from cryptography.fernet import Fernet

        return Fernet
    except ImportError:
        return None


class LocalEncryptedTokenStore:
    """Stores tokens in an encrypted JSON file on the local filesystem.

    Encryption uses Fernet (AES-128-CBC + HMAC-SHA256) from the
    ``cryptography`` package.  If the package is not installed the store
    falls back to plaintext JSON and logs a warning.

    The encryption key is stored alongside the token file with a ``.key``
    extension.  Both files should be kept in a directory with mode 0700.

    Args:
        path: Path to the token JSON file (or its encrypted equivalent).
              Defaults to ``~/.ravn/mcp_tokens.json``.
    """

    def __init__(self, path: str = "~/.ravn/mcp_tokens.json") -> None:
        self._path = Path(path).expanduser().resolve()
        self._key_path = self._path.with_suffix(".key")
        self._Fernet = _try_import_fernet()
        if self._Fernet is None:
            logger.warning(
                "cryptography package not installed — MCP tokens will be stored "
                "as plaintext JSON at %s. Install the 'encryption' extra for "
                "Fernet-encrypted storage.",
                self._path,
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def load(self, server_name: str) -> MCPToken | None:
        data = self._read_all()
        raw = data.get(server_name)
        if raw is None:
            return None
        return MCPToken.from_dict(raw)

    async def save(self, server_name: str, token: MCPToken) -> None:
        data = self._read_all()
        data[server_name] = token.to_dict()
        self._write_all(data)

    async def delete(self, server_name: str) -> None:
        data = self._read_all()
        data.pop(server_name, None)
        self._write_all(data)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _read_all(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            raw_bytes = self._path.read_bytes()
            if self._Fernet is not None:
                fernet = self._Fernet(self._load_key())
                raw_bytes = fernet.decrypt(raw_bytes)
            return json.loads(raw_bytes.decode())
        except Exception as exc:
            logger.warning("Failed to read token store %s: %s", self._path, exc)
            return {}

    def _write_all(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data).encode()
        if self._Fernet is not None:
            fernet = self._Fernet(self._load_or_create_key())
            payload = fernet.encrypt(payload)
        self._path.write_bytes(payload)
        self._path.chmod(0o600)

    def _load_key(self) -> bytes:
        if not self._key_path.exists():
            return self._load_or_create_key()
        return self._key_path.read_bytes()

    def _load_or_create_key(self) -> bytes:
        if self._key_path.exists():
            return self._key_path.read_bytes()
        key = self._Fernet.generate_key()
        self._key_path.parent.mkdir(parents=True, exist_ok=True)
        self._key_path.write_bytes(key)
        self._key_path.chmod(0o600)
        return key


# ---------------------------------------------------------------------------
# OpenBao token store (infra mode)
# ---------------------------------------------------------------------------


class OpenBaoTokenStore:
    """Stores tokens in an OpenBao (Vault-compatible) KV v2 secret.

    Tokens are stored at ``{mount}/data/{path_prefix}/{server_name}``.

    Args:
        url:           OpenBao base URL (e.g. ``http://openbao:8200``).
        token:         OpenBao root/service token.  Prefer passing via
                       *token_env* instead.
        token_env:     Environment variable name holding the OpenBao token.
        mount:         KV secrets engine mount path (default ``secret``).
        path_prefix:   Sub-path prefix (default ``ravn/mcp``).
    """

    def __init__(
        self,
        url: str = "http://openbao:8200",
        token: str = "",
        token_env: str = "OPENBAO_TOKEN",
        mount: str = "secret",
        path_prefix: str = "ravn/mcp",
    ) -> None:
        self._url = url.rstrip("/")
        self._token = token or os.environ.get(token_env, "")
        self._mount = mount
        self._path_prefix = path_prefix

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def load(self, server_name: str) -> MCPToken | None:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    self._secret_url(server_name),
                    headers=self._headers(),
                )
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                secret_data = response.json()["data"]["data"]
                return MCPToken.from_dict(secret_data)
        except Exception as exc:
            logger.warning("OpenBao load failed for %r: %s", server_name, exc)
            return None

    async def save(self, server_name: str, token: MCPToken) -> None:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self._secret_url(server_name),
                    headers=self._headers(),
                    json={"data": token.to_dict()},
                )
                response.raise_for_status()
        except Exception as exc:
            logger.warning("OpenBao save failed for %r: %s", server_name, exc)

    async def delete(self, server_name: str) -> None:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.delete(
                    self._secret_url(server_name),
                    headers=self._headers(),
                )
                response.raise_for_status()
        except Exception as exc:
            logger.debug("OpenBao delete failed for %r: %s", server_name, exc)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _secret_url(self, server_name: str) -> str:
        return f"{self._url}/v1/{self._mount}/data/{self._path_prefix}/{server_name}"

    def _headers(self) -> dict[str, str]:
        return {"X-Vault-Token": self._token, "Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# Auth flow implementations
# ---------------------------------------------------------------------------


async def acquire_api_key(api_key_env: str, api_key_header: str, api_key_prefix: str) -> MCPToken:
    """Acquire an API-key token from the environment.

    The key value is read from the environment variable named *api_key_env*.
    Bifrost or the RAVN.md secrets block injects these variables before the
    agent starts, so the agent never needs to handle raw secret values.

    Args:
        api_key_env:    Name of the environment variable holding the API key.
        api_key_header: HTTP header used to send the key (usually
                        ``Authorization``).
        api_key_prefix: Value prefix (e.g. ``Bearer``, ``ApiKey``).

    Returns:
        An ``MCPToken`` that never expires (API keys don't rotate on their own).

    Raises:
        ValueError: If the environment variable is not set or is empty.
    """
    value = os.environ.get(api_key_env, "").strip()
    if not value:
        raise ValueError(
            f"API key environment variable {api_key_env!r} is not set. "
            "Ensure the key is configured in the RAVN.md secrets block or "
            "injected via Bifrost."
        )
    return MCPToken(access_token=value, token_type=api_key_prefix, expires_at=None)


async def acquire_client_credentials(
    token_url: str,
    client_id: str,
    client_secret: str,
    scope: str = "",
    audience: str = "",
) -> MCPToken:
    """Acquire a token via OAuth 2.0 client-credentials grant.

    Suitable for machine-to-machine MCP servers that do not require user
    interaction.

    Args:
        token_url:     Token endpoint URL.
        client_id:     OAuth client ID.
        client_secret: OAuth client secret.
        scope:         Space-separated OAuth scope string (optional).
        audience:      Audience claim to request (optional).

    Returns:
        An ``MCPToken`` with an ``expires_at`` timestamp derived from the
        server's ``expires_in`` response field (if present).

    Raises:
        RuntimeError: If the token request fails or the response is invalid.
    """
    form: dict[str, str] = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    if scope:
        form["scope"] = scope
    if audience:
        form["audience"] = audience

    async with httpx.AsyncClient() as client:
        response = await client.post(
            token_url,
            data=form,
            headers={"Accept": "application/json"},
        )
        if not response.is_success:
            raise RuntimeError(
                f"Token request failed ({response.status_code}): {response.text[:200]}"
            )
        payload = response.json()

    access_token = payload.get("access_token", "")
    if not access_token:
        raise RuntimeError(f"Token response missing 'access_token': {payload}")

    expires_in = payload.get("expires_in")
    expires_at = (time.time() + float(expires_in)) if expires_in is not None else None
    token_type = payload.get("token_type", "Bearer")

    return MCPToken(access_token=access_token, token_type=token_type, expires_at=expires_at)


async def acquire_device_flow(
    token_url: str,
    client_id: str,
    scope: str = "",
    poll_interval: float = _DEVICE_POLL_INTERVAL_SECONDS,
    max_attempts: int = _DEVICE_POLL_MAX_ATTEMPTS,
) -> tuple[MCPToken, str]:
    """Acquire a token via the OAuth 2.0 Device Authorization Grant.

    Ravn initiates the device authorization request, then returns
    user-facing instructions *before* polling.  The caller is expected to
    surface those instructions (e.g. via ``ask_user`` or a channel message)
    so the user can approve access in their browser.

    Args:
        token_url:     Token endpoint URL (also used to derive the device
                       authorization endpoint if not explicitly given).
        client_id:     OAuth client ID.
        scope:         Space-separated OAuth scope string (optional).
        poll_interval: Seconds between polling attempts (default 5).
        max_attempts:  Maximum polling attempts before giving up (default 60).

    Returns:
        A tuple of ``(MCPToken, user_instructions)`` where *user_instructions*
        is a human-readable string describing the steps the user must take.

    Raises:
        RuntimeError: If the device flow fails or times out.
    """
    # Derive device authorization URL from token URL.
    device_url = token_url.replace("/token", "/device/code")

    form: dict[str, str] = {"client_id": client_id}
    if scope:
        form["scope"] = scope

    async with httpx.AsyncClient() as client:
        resp = await client.post(device_url, data=form, headers={"Accept": "application/json"})
        if not resp.is_success:
            raise RuntimeError(
                f"Device authorization request failed ({resp.status_code}): {resp.text[:200]}"
            )
        device_resp = resp.json()

    user_code = device_resp.get("user_code", "")
    verification_uri = device_resp.get("verification_uri") or device_resp.get(
        "verification_url", ""
    )
    device_code = device_resp.get("device_code", "")
    interval = float(device_resp.get("interval", poll_interval))

    instructions = (
        f"To authenticate with this MCP server:\n"
        f"  1. Visit: {verification_uri}\n"
        f"  2. Enter code: {user_code}\n"
        f"  3. Approve the access request.\n"
        f"Waiting for approval (checking every {interval:.0f}s)…"
    )

    # Poll for token.
    token = await _poll_device_token(
        token_url=token_url,
        client_id=client_id,
        device_code=device_code,
        interval=interval,
        max_attempts=max_attempts,
    )
    return token, instructions


async def _poll_device_token(
    token_url: str,
    client_id: str,
    device_code: str,
    interval: float,
    max_attempts: int,
) -> MCPToken:
    """Poll the token endpoint until the device code is approved."""
    form = {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "client_id": client_id,
        "device_code": device_code,
    }

    async with httpx.AsyncClient() as client:
        for attempt in range(max_attempts):
            if attempt > 0:
                await asyncio.sleep(interval)

            resp = await client.post(
                token_url,
                data=form,
                headers={"Accept": "application/json"},
            )
            payload = resp.json()

            error = payload.get("error", "")
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval = min(interval * 2, 30.0)
                continue
            if error:
                description = payload.get("error_description", "")
                raise RuntimeError(f"Device flow error: {error} — {description}")

            access_token = payload.get("access_token", "")
            if not access_token:
                raise RuntimeError(f"Unexpected token response: {payload}")

            expires_in = payload.get("expires_in")
            expires_at = (time.time() + float(expires_in)) if expires_in is not None else None
            return MCPToken(
                access_token=access_token,
                token_type=payload.get("token_type", "Bearer"),
                expires_at=expires_at,
            )

    raise RuntimeError(
        f"Device flow timed out after {max_attempts} attempts "
        f"({max_attempts * interval:.0f}s total)"
    )


# ---------------------------------------------------------------------------
# Auth session (per-agent-session cache)
# ---------------------------------------------------------------------------


@dataclass
class _SessionEntry:
    """In-memory cache entry for a single server's auth state."""

    token: MCPToken
    auth_type: MCPAuthType


class MCPAuthSession:
    """Per-agent-session authentication state.

    Wraps a persistent ``MCPTokenStore`` with an in-memory cache so that
    repeated tool calls don't hit the token store on every invocation.
    Expired tokens trigger a transparent refresh on the next
    ``get_auth_headers()`` call.

    Args:
        store: Persistent token backend (``LocalEncryptedTokenStore`` or
               ``OpenBaoTokenStore``).
    """

    def __init__(self, store: MCPTokenStore) -> None:
        self._store = store
        self._cache: dict[str, _SessionEntry] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_token(self, server_name: str) -> MCPToken | None:
        """Return the in-memory token for *server_name*, or None."""
        entry = self._cache.get(server_name)
        if entry is None:
            stored = await self._store.load(server_name)
            if stored is not None:
                self._cache[server_name] = _SessionEntry(
                    token=stored, auth_type=MCPAuthType.API_KEY
                )
            return stored
        return entry.token

    def get_auth_headers(self, server_name: str) -> dict[str, str]:
        """Return HTTP auth headers for *server_name* if a token is cached.

        Returns an empty dict when no token is available so callers can safely
        merge without extra None checks.
        """
        entry = self._cache.get(server_name)
        if entry is None or entry.token.is_expired():
            return {}
        return entry.token.as_auth_headers()

    async def authenticate(
        self,
        server_name: str,
        auth_type: MCPAuthType,
        *,
        # api_key params
        api_key_env: str = "",
        api_key_header: str = "Authorization",
        api_key_prefix: str = "Bearer",
        # oauth params
        token_url: str = "",
        client_id: str = "",
        client_secret: str = "",
        scope: str = "",
        audience: str = "",
        # device flow tuning (tests override these)
        device_poll_interval: float = _DEVICE_POLL_INTERVAL_SECONDS,
        device_max_attempts: int = _DEVICE_POLL_MAX_ATTEMPTS,
    ) -> tuple[MCPToken, str]:
        """Run the appropriate auth flow and cache the resulting token.

        Returns:
            A tuple of ``(token, message)`` where *message* is a
            human-readable status string for the agent to relay to the user.
        """
        match auth_type:
            case MCPAuthType.API_KEY:
                token = await acquire_api_key(api_key_env, api_key_header, api_key_prefix)
                message = f"API key authenticated for MCP server {server_name!r}."

            case MCPAuthType.CLIENT_CREDENTIALS:
                token = await acquire_client_credentials(
                    token_url=token_url,
                    client_id=client_id,
                    client_secret=client_secret,
                    scope=scope,
                    audience=audience,
                )
                message = (
                    f"Client-credentials token acquired for MCP server {server_name!r}. "
                    f"Token type: {token.token_type}."
                )

            case MCPAuthType.DEVICE_FLOW:
                token, instructions = await acquire_device_flow(
                    token_url=token_url,
                    client_id=client_id,
                    scope=scope,
                    poll_interval=device_poll_interval,
                    max_attempts=device_max_attempts,
                )
                message = (
                    f"Device-flow authentication complete for MCP server {server_name!r}.\n"
                    f"{instructions}"
                )

        await self._store.save(server_name, token)
        self._cache[server_name] = _SessionEntry(token=token, auth_type=auth_type)
        logger.info("Authenticated MCP server %r", server_name)
        return token, message

    async def revoke(self, server_name: str) -> None:
        """Remove stored and cached tokens for *server_name*."""
        self._cache.pop(server_name, None)
        await self._store.delete(server_name)
        logger.info("Revoked auth for MCP server %r", server_name)
