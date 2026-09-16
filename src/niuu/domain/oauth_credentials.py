"""Stable identities shared by OAuth storage and workload secret projection."""

import hashlib
import json

OAUTH_ENGINE = "openbao_oauthapp"


def oauth_credential_name(tenant_id: str, owner_id: str, name: str) -> str:
    if not tenant_id or not owner_id or not name:
        raise ValueError("OAuth credentials require tenant, owner, and credential identity")
    identity = json.dumps([tenant_id, owner_id, name], separators=(",", ":"))
    return hashlib.sha256(identity.encode()).hexdigest()


def mcp_token_path(connection_id: str) -> str:
    return f"/run/secrets/mcp/{hashlib.sha256(connection_id.encode()).hexdigest()}/token"


class OAuthCredentialUnavailableError(RuntimeError):
    """Safe engine failure, distinguished from a grant requiring reconnection."""

    def __init__(self, *, reconnect: bool = False):
        self.reconnect = reconnect
        super().__init__(
            "OAuth authorization unavailable; reconnect the integration"
            if reconnect
            else "OpenBao OAuth credential unavailable"
        )


def oauth_application_name(slug: str, app: str) -> str:
    identity = json.dumps([slug, app], separators=(",", ":"))
    return "niuu-" + hashlib.sha256(identity.encode()).hexdigest()


def mcp_token_env(connection_id: str) -> str:
    return "NIUU_MCP_" + hashlib.sha256(connection_id.encode()).hexdigest().upper()
