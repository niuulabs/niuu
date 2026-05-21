#!/usr/bin/env python3
"""Idempotent OpenBao bootstrap/reconcile helper.

Reads a YAML spec and ensures:
- KV v2 mounts
- JWT auth backends
- JWT auth config
- ACL policies
- explicit JWT roles

This is designed for lightweight Kubernetes Jobs or CI runners.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from typing import Any

import yaml

from volundr.adapters.outbound.openbao import (
    OpenBaoAdminClient,
    OpenBaoAdminConfig,
    OpenBaoJWTAuthConfig,
    OpenBaoJWTAuthRole,
)


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Required environment variable is missing: {name}")
    return value


def _load_spec(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise SystemExit(f"Bootstrap spec must be a YAML mapping: {path}")
    return data


def _build_admin_config(spec: dict[str, Any]) -> OpenBaoAdminConfig:
    bao = spec.get("openbao", {})
    if not isinstance(bao, dict):
        raise SystemExit("openbao section must be a mapping")

    auth = bao.get("auth", {})
    if not isinstance(auth, dict):
        raise SystemExit("openbao.auth section must be a mapping")

    auth_method = str(auth.get("method", "token"))
    token = ""
    role_id = ""
    secret_id = ""

    if auth_method == "token":
        token = _require_env(str(auth.get("token_env", "OPENBAO_TOKEN")))
    elif auth_method == "approle":
        role_id = _require_env(str(auth.get("role_id_env", "OPENBAO_ROLE_ID")))
        secret_id = _require_env(str(auth.get("secret_id_env", "OPENBAO_SECRET_ID")))
    else:
        raise SystemExit(f"Unsupported auth method: {auth_method}")

    return OpenBaoAdminConfig(
        url=str(bao.get("url", "http://openbao:8200")),
        namespace=str(bao.get("namespace", "")),
        auth_method=auth_method,
        token=token,
        approle_mount_path=str(auth.get("approle_mount_path", "auth/approle")),
        role_id=role_id,
        secret_id=secret_id,
    )


async def _run(spec: dict[str, Any]) -> None:
    client = OpenBaoAdminClient(_build_admin_config(spec))
    try:
        for mount in spec.get("mounts", []):
            await client.ensure_kv_v2_mount(
                mount_path=str(mount["path"]),
                description=str(mount.get("description", "")),
            )

        for backend in spec.get("jwtAuthBackends", []):
            path = str(backend["path"])
            await client.ensure_jwt_auth_backend(
                path=path,
                description=str(backend.get("description", "")),
            )
            await client.configure_jwt_auth(
                OpenBaoJWTAuthConfig(
                    path=path,
                    oidc_discovery_url=str(backend["oidc_discovery_url"]),
                    bound_issuer=str(backend.get("bound_issuer", "")),
                    default_role=str(backend.get("default_role", "")),
                    oidc_discovery_ca_pem=str(backend.get("oidc_discovery_ca_pem", "")),
                    jwt_supported_algs=tuple(backend.get("jwt_supported_algs", ["RS256"])),
                )
            )

        for policy in spec.get("policies", []):
            await client.ensure_policy(
                str(policy["name"]),
                str(policy["policy"]),
            )

        for role in spec.get("jwtRoles", []):
            await client.ensure_jwt_role(
                OpenBaoJWTAuthRole(
                    name=str(role["name"]),
                    auth_path=str(role.get("auth_path", "jwt")),
                    user_claim=str(role.get("user_claim", "sub")),
                    role_type=str(role.get("role_type", "jwt")),
                    bound_audiences=tuple(role.get("bound_audiences", ["openbao"])),
                    bound_subject=str(role.get("bound_subject", "")),
                    policies=tuple(role.get("policies", [])),
                    ttl=str(role.get("ttl", "1h")),
                    bound_claims=dict(role.get("bound_claims", {})),
                )
            )
    finally:
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path, help="Path to the OpenBao bootstrap YAML spec")
    args = parser.parse_args()

    spec = _load_spec(args.spec)
    asyncio.run(_run(spec))


if __name__ == "__main__":
    main()
