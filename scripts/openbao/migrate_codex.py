#!/usr/bin/env python3
"""Migrate one verified user/tenant's Codex grant after replacing legacy brokers."""

import argparse
import asyncio
import importlib
import json
from pathlib import Path

import yaml

from niuu.adapters.openbao_oauth_credential_store import OpenBaoOAuthCredentialStore
from niuu.domain.codex_credentials import CODEX_AUTH_FORMAT, parse_codex_auth_document
from niuu.domain.oauth_credentials import OAUTH_ENGINE


async def migrate(args):
    config = yaml.safe_load(Path(args.config).read_text())["credential_store"]
    module, name = config["adapter"].rsplit(".", 1)
    store = getattr(importlib.import_module(module), name)(**config.get("kwargs", {}))
    if not isinstance(store, OpenBaoOAuthCredentialStore):
        raise ValueError("Configure the OpenBao OAuth credential store before migration")
    try:
        stored = await store.get("user", args.owner, args.credential)
        if stored is None:
            raise ValueError("Credential does not exist")
        if stored.metadata.get("tenant_id") not in {None, "", args.tenant}:
            raise ValueError("Credential tenant does not match")
        managed = stored.metadata.get("oauth_format") == CODEX_AUTH_FORMAT
        if stored.metadata.get("renewal_owner") and not managed:
            raise ValueError("Credential already has a different renewal owner")
        if not managed:
            values = await store.get_value("user", args.owner, args.credential)
            parse_codex_auth_document((values or {}).get("auth.json"), require_refresh=True)
        changed = False
        if args.apply:
            changed = await store.migrate_codex_credential(
                owner_id=args.owner, tenant_id=args.tenant, name=args.credential
            )
            updated = await store.get("user", args.owner, args.credential)
            assert updated.metadata["renewal_owner"] == OAUTH_ENGINE
        print(json.dumps({"applied": args.apply, "migrated": changed, "already_managed": managed}))
    finally:
        await store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", required=True, help="Existing application YAML with workload auth"
    )
    parser.add_argument("--owner", required=True, help="Verified credential owner ID")
    parser.add_argument("--tenant", required=True, help="Verified tenant membership of this owner")
    parser.add_argument("--credential", required=True, help="Existing Codex credential name")
    parser.add_argument("--apply", action="store_true", help="Import and rotate the real grant")
    args = parser.parse_args()
    try:
        asyncio.run(migrate(args))
    except Exception as exc:
        # Provider/transport errors must never dump credential-bearing bodies.
        raise SystemExit(
            f"Migration failed ({type(exc).__name__}); inspect vault audit status"
        ) from None


if __name__ == "__main__":
    main()
