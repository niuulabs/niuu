"""OIDC/PKCE authentication client for CLI login flow."""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Event, Thread
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import jwt

from cli.auth.credentials import CredentialStore, StoredTokens

logger = logging.getLogger(__name__)

PKCE_VERIFIER_LENGTH = 64
CALLBACK_TIMEOUT_SECONDS = 120
LOCAL_CALLBACK_HOST = "127.0.0.1"


def generate_pkce_pair() -> tuple[str, str]:
    """Generate a PKCE code_verifier and code_challenge (S256).

    Returns (verifier, challenge).
    """
    verifier = secrets.token_urlsafe(PKCE_VERIFIER_LENGTH)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def decode_id_token(token: str) -> dict[str, Any]:
    """Decode a JWT id_token without verification (for display only)."""
    return jwt.decode(token, options={"verify_signature": False})


class OIDCClient:
    """OIDC Authorization Code + PKCE flow for CLI login.

    Discovers endpoints from the issuer's well-known configuration,
    opens a browser for authentication, and exchanges the code for tokens.
    """

    def __init__(
        self,
        issuer: str,
        client_id: str,
        scopes: str = "openid profile email",
        credential_store: CredentialStore | None = None,
    ) -> None:
        self._issuer = issuer.rstrip("/")
        self._client_id = client_id
        self._scopes = scopes
        self._credential_store = credential_store or CredentialStore()
        self._discovery: dict[str, Any] = {}

    async def discover(self) -> dict[str, Any]:
        """Fetch OIDC discovery document."""
        if self._discovery:
            return self._discovery
        url = f"{self._issuer}/.well-known/openid-configuration"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            self._discovery = resp.json()
        return self._discovery

    async def login(self) -> StoredTokens:
        """Run the full OIDC/PKCE login flow.

        1. Discover endpoints.
        2. Generate PKCE verifier/challenge.
        3. Start local callback server.
        4. Open browser to authorization URL.
        5. Wait for callback with auth code.
        6. Exchange code for tokens.
        7. Persist tokens.
        """
        discovery = await self.discover()
        authorization_endpoint = discovery["authorization_endpoint"]
        token_endpoint = discovery["token_endpoint"]

        verifier, challenge = generate_pkce_pair()

        callback_received = Event()
        result: dict[str, str] = {}

        expected_state = secrets.token_urlsafe(32)
        handler_factory = _make_callback_handler(callback_received, result, expected_state)
        server = HTTPServer((LOCAL_CALLBACK_HOST, 0), handler_factory)
        port = server.server_address[1]
        redirect_uri = f"http://{LOCAL_CALLBACK_HOST}:{port}/callback"

        server_thread = Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        try:
            auth_params = urlencode(
                {
                    "response_type": "code",
                    "client_id": self._client_id,
                    "redirect_uri": redirect_uri,
                    "scope": self._scopes,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "state": expected_state,
                }
            )
            auth_url = f"{authorization_endpoint}?{auth_params}"
            webbrowser.open(auth_url)

            if not callback_received.wait(timeout=CALLBACK_TIMEOUT_SECONDS):
                raise TimeoutError("Authentication callback not received in time")

            if "error" in result:
                raise RuntimeError(
                    f"OIDC error: {result['error']} — {result.get('error_description', '')}"
                )

            code = result["code"]
            tokens = await self._exchange_code(
                token_endpoint,
                code,
                redirect_uri,
                verifier,
            )
            self._credential_store.store(tokens)
            return tokens
        finally:
            server.shutdown()
            server.server_close()

    async def _exchange_code(
        self,
        token_endpoint: str,
        code: str,
        redirect_uri: str,
        verifier: str,
    ) -> StoredTokens:
        """Exchange authorization code for tokens."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "client_id": self._client_id,
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "code_verifier": verifier,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        expires_at = 0.0
        if "expires_in" in data:
            expires_at = time.time() + data["expires_in"]

        return StoredTokens(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", ""),
            id_token=data.get("id_token", ""),
            token_type=data.get("token_type", "Bearer"),
            expires_at=expires_at,
            issuer=self._issuer,
        )

    async def refresh(self) -> str | None:
        """Refresh the access token using stored refresh_token.

        Returns the new access token, or None if refresh fails.
        """
        tokens = self._credential_store.load()
        if not tokens or not tokens.refresh_token:
            return None

        discovery = await self.discover()
        token_endpoint = discovery["token_endpoint"]

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    token_endpoint,
                    data={
                        "grant_type": "refresh_token",
                        "client_id": self._client_id,
                        "refresh_token": tokens.refresh_token,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            logger.debug("token refresh failed", exc_info=True)
            return None

        expires_at = 0.0
        if "expires_in" in data:
            expires_at = time.time() + data["expires_in"]

        new_tokens = StoredTokens(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", tokens.refresh_token),
            id_token=data.get("id_token", tokens.id_token),
            token_type=data.get("token_type", "Bearer"),
            expires_at=expires_at,
            issuer=tokens.issuer,
        )
        self._credential_store.store(new_tokens)
        return new_tokens.access_token

    def logout(self) -> None:
        """Clear all stored credentials."""
        self._credential_store.clear()

    def whoami(self) -> dict[str, Any] | None:
        """Decode the stored id_token and return user claims, or None."""
        tokens = self._credential_store.load()
        if not tokens or not tokens.id_token:
            return None
        try:
            return decode_id_token(tokens.id_token)
        except Exception:
            logger.debug("failed to decode id_token", exc_info=True)
            return None

    def load_access_token(self) -> str | None:
        """Load the stored access token if present."""
        tokens = self._credential_store.load()
        if not tokens:
            return None
        return tokens.access_token


def _make_callback_handler(
    callback_received: Event,
    result: dict[str, str],
    expected_state: str,
) -> type[BaseHTTPRequestHandler]:
    """Create an HTTP request handler that captures the OIDC callback."""

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)

            # Validate state to prevent CSRF.
            returned_state = params.get("state", [""])[0]
            if returned_state != expected_state:
                result["error"] = "state_mismatch"
                result["error_description"] = "OIDC state parameter does not match"
                self.send_response(400)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                body = (
                    "<html><body><h1>Authentication failed</h1>"
                    "<p>State mismatch — possible CSRF attack.</p></body></html>"
                )
                self.wfile.write(body.encode())
                callback_received.set()
                return

            if "code" in params:
                result["code"] = params["code"][0]
            if "error" in params:
                result["error"] = params["error"][0]
                result["error_description"] = params.get("error_description", [""])[0]

            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            body = (
                "<html><body><h1>Authentication complete</h1>"
                "<p>You can close this window.</p></body></html>"
            )
            self.wfile.write(body.encode())
            callback_received.set()

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            logger.debug(format, *args)

    return CallbackHandler
