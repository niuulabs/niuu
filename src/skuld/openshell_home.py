"""Prepare agent CLI home files backed by OpenShell runtime credentials."""

from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

CODEX_ACCESS_TOKEN_ENV = "CODEX_AUTH_ACCESS_TOKEN"
CODEX_ACCOUNT_ID_ENV = "CODEX_AUTH_ACCOUNT_ID"
CODEX_REFRESH_TOKEN_REFERENCE = "openshell:resolve:env:CODEX_AUTH_REFRESH_TOKEN"
METADATA_TOKEN_LIFETIME = timedelta(hours=1)
METADATA_TOKEN_SIGNATURE = "b3BlbnNoZWxs"


def prepare_codex_home(
    *,
    codex_home: Path,
    access_token_reference: str,
    account_id: str,
    now: datetime | None = None,
) -> Path:
    """Write the non-secret Codex auth envelope used by OpenShell's proxy."""
    if not access_token_reference.startswith("openshell:resolve:env:"):
        raise RuntimeError("Codex access token must be an OpenShell credential reference")
    if not account_id:
        raise RuntimeError("Codex account ID is required")

    generated_at = now or datetime.now(UTC)
    id_token = _metadata_id_token(account_id, generated_at)
    document = {
        "auth_mode": "chatgpt",
        "OPENAI_API_KEY": None,
        "tokens": {
            "id_token": id_token,
            "access_token": access_token_reference,
            "refresh_token": CODEX_REFRESH_TOKEN_REFERENCE,
            "account_id": account_id,
        },
        "last_refresh": generated_at.isoformat(),
    }

    codex_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    codex_home.chmod(0o700)
    destination = codex_home / "auth.json"
    destination.write_text(json.dumps(document, separators=(",", ":")), encoding="utf-8")
    destination.chmod(0o600)
    return destination


def prepare_from_environment() -> Path | None:
    """Prepare Codex state when an OpenShell Codex credential is attached."""
    access_token_reference = os.environ.get(CODEX_ACCESS_TOKEN_ENV, "")
    account_id = os.environ.get(CODEX_ACCOUNT_ID_ENV, "")
    if not access_token_reference and not account_id:
        return None
    codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    return prepare_codex_home(
        codex_home=codex_home,
        access_token_reference=access_token_reference,
        account_id=account_id,
    )


def _metadata_id_token(account_id: str, now: datetime) -> str:
    """Build the non-credential JWT shape Codex uses for local account metadata."""
    issued_at = int(now.timestamp())
    expires_at = int((now + METADATA_TOKEN_LIFETIME).timestamp())
    header = _base64url({"alg": "none", "typ": "JWT"})
    claims = _base64url(
        {
            "iss": "https://auth.openai.com",
            "aud": "codex",
            "sub": "openshell-runtime",
            "iat": issued_at,
            "exp": expires_at,
            "https://api.openai.com/auth.chatgpt_account_id": account_id,
        }
    )
    return f"{header}.{claims}.{METADATA_TOKEN_SIGNATURE}"


def _base64url(value: dict[str, object]) -> str:
    encoded = json.dumps(value, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")


if __name__ == "__main__":
    prepare_from_environment()
