#!/usr/bin/env python3
"""Bootstrap helpers for the local multi-instance Guild stack.

This script does two jobs:
1. Merge the user's normal ``~/.niuu/config.yaml`` into the local guild configs
   so the stack inherits the same auth, git, and tracker setup as ``start-dev``.
2. Seed per-user Ting tracker integrations for local dev identities so the
   tracker import UI has real data to browse.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import httpx
import yaml


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


def merge_configs(
    base_config: Path,
    worker_overlay: Path,
    central_overlay: Path,
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    base = _load_yaml(base_config)
    worker = _deep_merge(base, _load_yaml(worker_overlay))
    central = _deep_merge(base, _load_yaml(central_overlay))

    (output_dir / "guild-worker.yaml").write_text(
        yaml.safe_dump(worker, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    (output_dir / "guild-central.yaml").write_text(
        yaml.safe_dump(central, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _headers(user_id: str, tenant_id: str) -> dict[str, str]:
    return {
        "x-auth-user-id": user_id,
        "x-auth-email": f"{user_id}@example.com",
        "x-auth-tenant": tenant_id,
        "x-auth-roles": "volundr:developer",
    }


def _integration_payload(api_key: str, team_id: str) -> dict[str, Any]:
    config: dict[str, Any] = {}
    if team_id:
        config["team_id"] = team_id
    return {
        "integration_type": "issue_tracker",
        "adapter": "ting.adapters.linear.LinearTrackerAdapter",
        "credential_name": "linear-config",
        "credential_value": api_key,
        "config": config,
    }


def seed_dev_integrations(
    *,
    base_url: str,
    base_config: Path,
    users: list[tuple[str, str]],
) -> None:
    config = _load_yaml(base_config)
    linear = config.get("linear")
    if not isinstance(linear, dict):
        return
    api_key = str(linear.get("api_key") or "").strip()
    team_id = str(linear.get("team_id") or "").strip()
    if not api_key:
        return

    payload = _integration_payload(api_key, team_id)
    with httpx.Client(timeout=20.0) as client:
        for user_id, tenant_id in users:
            headers = _headers(user_id, tenant_id)
            list_response = client.get(
                f"{base_url}/api/v1/ting/integrations",
                headers=headers,
            )
            list_response.raise_for_status()
            existing = list_response.json()
            if any(
                item.get("integration_type") == "issue_tracker"
                and item.get("adapter") == "ting.adapters.linear.LinearTrackerAdapter"
                for item in existing
            ):
                continue
            create_response = client.post(
                f"{base_url}/api/v1/ting/integrations",
                headers=headers,
                json=payload,
            )
            create_response.raise_for_status()


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    merge_parser = subparsers.add_parser("merge-configs")
    merge_parser.add_argument("--base-config", type=Path, required=True)
    merge_parser.add_argument("--worker-overlay", type=Path, required=True)
    merge_parser.add_argument("--central-overlay", type=Path, required=True)
    merge_parser.add_argument("--output-dir", type=Path, required=True)

    seed_parser = subparsers.add_parser("seed-dev-integrations")
    seed_parser.add_argument("--base-url", required=True)
    seed_parser.add_argument("--base-config", type=Path, required=True)
    seed_parser.add_argument(
        "--users-json",
        required=True,
        help='JSON array like [["guild-user-a","tenant-a"],["guild-user-b","tenant-b"]]',
    )

    args = parser.parse_args()
    if args.command == "merge-configs":
        merge_configs(args.base_config, args.worker_overlay, args.central_overlay, args.output_dir)
        return

    users_raw = json.loads(args.users_json)
    users = [(str(item[0]), str(item[1])) for item in users_raw]
    seed_dev_integrations(
        base_url=args.base_url.rstrip("/"),
        base_config=args.base_config,
        users=users,
    )


if __name__ == "__main__":
    main()
