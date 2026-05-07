"""Shared connection testing for integration types.

Tests connections by calling the appropriate health/identity endpoints.
Used by both Tyr and Volundr integration management APIs.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

_ALLOWED_SCHEMES = {"http", "https"}
_TELEGRAM_BOT_TOKEN_RE = re.compile(r"^[0-9]{6,}:[A-Za-z0-9_-]{20,}$")


def _is_valid_telegram_bot_token(bot_token: str) -> bool:
    """Validate Telegram bot token format before using it in URL construction."""
    return bool(_TELEGRAM_BOT_TOKEN_RE.fullmatch(bot_token))


@dataclass(frozen=True)
class ConnectionTestResult:
    """Result of testing an integration connection."""

    success: bool
    message: str
    provider: str = ""
    user: str = ""


async def test_code_forge(url: str, token: str) -> ConnectionTestResult:
    """Test a Volundr/code forge connection via /me endpoint."""
    test_url = url.rstrip("/")
    if not test_url:
        return ConnectionTestResult(success=False, message="No URL configured")
    parsed = urlparse(test_url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        return ConnectionTestResult(
            success=False,
            message=f"Unsupported URL scheme: {parsed.scheme!r}",
            provider="volundr",
        )
    if not parsed.hostname:
        return ConnectionTestResult(
            success=False, message="Invalid URL: no hostname", provider="volundr"
        )
    # Reconstruct from validated components to satisfy SSRF analysis
    safe_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{safe_url}/api/v1/identity/me",
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                user = data.get("email") or data.get("user_id") or "authenticated"
                return ConnectionTestResult(
                    success=True,
                    message=f"Connected as {user}",
                    provider="volundr",
                    user=user,
                )
            return ConnectionTestResult(
                success=False,
                message=f"Authentication failed (HTTP {resp.status_code})",
                provider="volundr",
            )
    except httpx.ConnectError:
        return ConnectionTestResult(
            success=False, message=f"Cannot reach {test_url}", provider="volundr"
        )
    except Exception as e:
        return ConnectionTestResult(success=False, message=str(e), provider="volundr")


async def test_telegram_bot(bot_token: str) -> ConnectionTestResult:
    """Test a Telegram bot token via getMe endpoint."""
    if not bot_token:
        return ConnectionTestResult(success=False, message="No bot token")
    if not _is_valid_telegram_bot_token(bot_token):
        return ConnectionTestResult(
            success=False,
            message="Invalid bot token format",
            provider="telegram",
        )
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://api.telegram.org/bot{bot_token}/getMe",
            )
            if resp.status_code == 200 and resp.json().get("ok"):
                bot_name = resp.json()["result"].get("username", "bot")
                return ConnectionTestResult(
                    success=True,
                    message=f"Connected as @{bot_name}",
                    provider="telegram",
                    user=f"@{bot_name}",
                )
            return ConnectionTestResult(
                success=False,
                message="Invalid bot token",
                provider="telegram",
            )
    except Exception as e:
        return ConnectionTestResult(success=False, message=str(e), provider="telegram")


async def test_connection(
    integration_type: str,
    config: dict,
    credentials: dict[str, str],
) -> ConnectionTestResult:
    """Test a connection based on its integration type.

    Args:
        integration_type: The type (code_forge, messaging, source_control, etc.)
        config: Adapter-specific config (e.g. {"url": "http://volundr"})
        credentials: Resolved credential values (e.g. {"token": "...", "bot_token": "..."})
    """
    if integration_type == "code_forge":
        return await test_code_forge(
            url=config.get("url", ""),
            token=credentials.get("token", ""),
        )

    if integration_type == "messaging":
        return await test_telegram_bot(
            bot_token=credentials.get("bot_token") or credentials.get("token", ""),
        )

    # For other types, just confirm credentials exist
    if credentials:
        return ConnectionTestResult(
            success=True, message="Credentials stored", provider=integration_type
        )
    return ConnectionTestResult(
        success=False, message="No credentials found", provider=integration_type
    )
