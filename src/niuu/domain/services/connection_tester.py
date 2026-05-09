"""Shared connection testing for integration types.

Tests connections by calling the appropriate health/identity endpoints.
Used by both Tyr and Volundr integration management APIs.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

import httpx

logger = logging.getLogger(__name__)

_ALLOWED_SCHEMES = {"http", "https"}
_TELEGRAM_BOT_TOKEN_RE = re.compile(r"^[0-9]{6,}:[A-Za-z0-9_-]{20,}$")


def _is_valid_telegram_bot_token(bot_token: str) -> bool:
    """Validate Telegram bot token format before using it in URL construction."""
    return bool(_TELEGRAM_BOT_TOKEN_RE.fullmatch(bot_token))


def _normalise_base_url(url: str) -> str:
    """Return a canonical base URL for trusted-server comparisons.

    Only http/https base URLs are allowed. Userinfo, query strings, and fragments
    are rejected because they should never participate in server-side credentialed
    health checks.
    """

    base_url = url.strip().rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme!r}")
    if not parsed.hostname:
        raise ValueError("Invalid URL: no hostname")
    if parsed.username or parsed.password:
        raise ValueError("Invalid URL: userinfo is not allowed")
    if parsed.query or parsed.fragment:
        raise ValueError("Invalid URL: query strings and fragments are not allowed")

    hostname = parsed.hostname.lower()
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"

    netloc = hostname
    if parsed.port is not None:
        netloc = f"{hostname}:{parsed.port}"

    path = parsed.path.rstrip("/")
    return urlunparse((parsed.scheme.lower(), netloc, path, "", "", ""))


def _resolve_trusted_code_forge_base_url(
    requested_url: str,
    trusted_base_urls: tuple[str, ...],
) -> str:
    """Return the matched trusted base URL, or raise when the target is untrusted.

    The outbound request must use the trusted configured base URL rather than the
    raw user-provided URL so credentials cannot be exfiltrated to arbitrary hosts.
    """

    requested_base = _normalise_base_url(requested_url)
    for trusted_url in trusted_base_urls:
        try:
            trusted_base = _normalise_base_url(trusted_url)
        except ValueError:
            logger.warning("Ignoring invalid trusted code forge base URL %r", trusted_url)
            continue
        if requested_base == trusted_base:
            return trusted_base

    raise ValueError(
        "Untrusted code forge URL: server-side credential tests only run against "
        "configured trusted base URLs"
    )


@dataclass(frozen=True)
class ConnectionTestResult:
    """Result of testing an integration connection."""

    success: bool
    message: str
    provider: str = ""
    user: str = ""


async def test_code_forge(
    url: str,
    token: str,
    *,
    trusted_base_urls: tuple[str, ...] = (),
) -> ConnectionTestResult:
    """Test a Volundr/code forge connection via /me endpoint."""
    test_url = url.rstrip("/")
    if not test_url:
        return ConnectionTestResult(success=False, message="No URL configured")

    try:
        trusted_url = _resolve_trusted_code_forge_base_url(test_url, trusted_base_urls)
    except ValueError as exc:
        return ConnectionTestResult(
            success=False,
            message=str(exc),
            provider="volundr",
        )

    try:
        async with httpx.AsyncClient(
            timeout=10.0,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            resp = await client.get(
                f"{trusted_url}/api/v1/identity/me",
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
    *,
    trusted_code_forge_base_urls: tuple[str, ...] = (),
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
            trusted_base_urls=trusted_code_forge_base_urls,
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
