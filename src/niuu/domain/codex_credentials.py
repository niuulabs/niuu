"""Codex credential document parsing shared by storage and token delivery."""

from __future__ import annotations

import json
import time
from typing import Any

import jwt

CODEX_AUTH_FORMAT = "codex_auth"


def parse_codex_auth_document(raw: object, *, require_refresh: bool = False) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("Codex auth document is unavailable")
    try:
        auth = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError("Codex auth document is invalid JSON") from None
    if not isinstance(auth, dict) or not isinstance(auth.get("tokens"), dict):
        raise ValueError("Codex auth document does not contain a token set")
    required = ["access_token", "account_id"]
    if require_refresh:
        required.append("refresh_token")
    if any(
        not isinstance(auth["tokens"].get(key), str) or not auth["tokens"][key] for key in required
    ):
        raise ValueError("Codex auth document is missing required OAuth fields")
    return auth


def codex_token_claims(token: str) -> dict[str, Any]:
    """Read metadata from a trusted provider token; this is not authentication."""
    try:
        return jwt.decode(
            token, options={"verify_signature": False, "verify_aud": False, "verify_exp": False}
        )
    except jwt.PyJWTError:
        raise ValueError("Codex OAuth access token is not a valid JWT") from None


def jwt_remaining_seconds(token: str) -> int:
    try:
        return int(codex_token_claims(token)["exp"]) - int(time.time())
    except (KeyError, TypeError, ValueError):
        raise ValueError("Codex OAuth access token is not a valid expiring JWT") from None


def codex_plan_type(auth: dict[str, Any], access_token: str) -> str:
    explicit = auth.get("chatgpt_plan_type") or auth.get("plan_type")
    if isinstance(explicit, str) and explicit:
        return explicit
    claims = codex_token_claims(access_token).get("https://api.openai.com/auth", {})
    return str(claims.get("chatgpt_plan_type") or "") if isinstance(claims, dict) else ""
