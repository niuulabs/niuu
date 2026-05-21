"""Smoke-test script for the canonical public route surface.

Runs a focused set of HTTP calls against the canonical API namespaces and
reports the returned status codes.

Usage:
    python -m tests.smoke_tests_route_parity --base-url http://localhost:8080 --token $TOKEN
    python -m tests.smoke_tests_route_parity --base-url http://localhost:8080 --domain forge
    python -m tests.smoke_tests_route_parity --base-url http://localhost:8080 --checklist
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from typing import Any

import httpx

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RouteCheck:
    name: str
    path: str
    method: str = "GET"
    params: dict[str, Any] = field(default_factory=dict)
    json_body: Any = None
    headers: dict[str, str] = field(default_factory=dict)
    domain: str = ""
    priority: str = "high"


ROUTE_CHECKS: list[RouteCheck] = [
    RouteCheck("identity/me", "/api/v1/identity/me", domain="identity"),
    RouteCheck("identity/tenants", "/api/v1/identity/tenants", domain="identity"),
    RouteCheck("identity/settings", "/api/v1/identity/settings", domain="identity"),
    RouteCheck("identity/users", "/api/v1/identity/users", domain="identity"),
    RouteCheck("tracker/status", "/api/v1/tracker/status", domain="tracker"),
    RouteCheck("tracker/repo-mappings", "/api/v1/tracker/repo-mappings", domain="tracker"),
    RouteCheck(
        "tracker/issues",
        "/api/v1/tracker/issues",
        params={"q": "test"},
        domain="tracker",
    ),
    RouteCheck("integrations/list", "/api/v1/integrations", domain="integrations"),
    RouteCheck("audit/events", "/api/v1/audit/events", params={"limit": 10}, domain="audit"),
    RouteCheck("forge/sessions", "/api/v1/forge/sessions", domain="forge"),
    RouteCheck("forge/chronicles", "/api/v1/forge/chronicles", domain="forge"),
    RouteCheck("forge/events", "/api/v1/forge/events", domain="forge"),
    RouteCheck("forge/templates", "/api/v1/forge/templates", domain="forge"),
    RouteCheck("forge/presets", "/api/v1/forge/presets", domain="forge"),
    RouteCheck("forge/profiles", "/api/v1/forge/profiles", domain="forge"),
    RouteCheck("forge/resources", "/api/v1/forge/resources", domain="forge"),
    RouteCheck("forge/prompts", "/api/v1/forge/prompts", domain="forge"),
    RouteCheck("forge/settings", "/api/v1/forge/settings", domain="forge"),
    RouteCheck("forge/stats", "/api/v1/forge/stats", domain="forge", priority="low"),
    RouteCheck("mimir/settings", "/api/v1/mimir/settings", domain="mimir"),
    RouteCheck("ting/settings", "/api/v1/ting/settings", domain="ting"),
    RouteCheck("ravn/settings", "/api/v1/ravn/settings", domain="ravn"),
    RouteCheck("observatory/settings", "/api/v1/observatory/settings", domain="observatory"),
    RouteCheck("bifrost/models", "/api/v1/bifrost/models", domain="bifrost"),
    RouteCheck("bifrost/settings", "/api/v1/bifrost/settings", domain="bifrost"),
    RouteCheck(
        "forge/repos/branches",
        "/api/v1/niuu/repos/branches",
        params={"repo_url": "github.com/acme/repo"},
        domain="forge",
    ),
    RouteCheck(
        "forge/repos/prs",
        "/api/v1/forge/repos/prs",
        params={"repo_url": "github.com/acme/repo"},
        domain="forge",
    ),
    RouteCheck("forge/git", "/api/v1/forge/git", domain="forge"),
    RouteCheck("forge/workspaces", "/api/v1/forge/workspaces", domain="forge"),
    RouteCheck("tokens/list", "/api/v1/tokens", domain="tokens"),
    RouteCheck("credentials/types", "/api/v1/credentials/types", domain="credentials"),
    RouteCheck("credentials/user", "/api/v1/credentials/user", domain="credentials"),
    RouteCheck("credentials/secrets", "/api/v1/credentials/secrets", domain="credentials"),
    RouteCheck(
        "credentials/mcp-servers",
        "/api/v1/credentials/mcp-servers",
        domain="credentials",
        priority="low",
    ),
    RouteCheck("features/modules", "/api/v1/features", domain="features", priority="medium"),
]


def _iter_checks(domain: str | None) -> list[RouteCheck]:
    if domain is None:
        return ROUTE_CHECKS
    return [check for check in ROUTE_CHECKS if check.domain == domain]


def _request(client: httpx.Client, check: RouteCheck, token: str | None) -> httpx.Response:
    headers = dict(check.headers)
    if token:
        headers.setdefault("Authorization", f"Bearer {token}")
    return client.request(
        check.method,
        check.path,
        params=check.params,
        json=check.json_body,
        headers=headers,
    )


def _print_checklist(domain: str | None) -> int:
    print("Canonical route checklist:")
    for idx, check in enumerate(_iter_checks(domain), start=1):
        print(f"{idx}. [{check.domain}] {check.method} {check.path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--token", default=None)
    parser.add_argument("--domain", default=None)
    parser.add_argument("--checklist", action="store_true")
    args = parser.parse_args(argv)

    if args.checklist:
        return _print_checklist(args.domain)

    checks = _iter_checks(args.domain)
    if not checks:
        print("No route checks matched the requested domain.", file=sys.stderr)
        return 1

    failures = 0
    with httpx.Client(base_url=args.base_url, follow_redirects=True, timeout=30.0) as client:
        for check in checks:
            response = _request(client, check, args.token)
            ok = response.status_code < 500
            status = "OK" if ok else "FAIL"
            print(f"{status:4} {response.status_code:3} {check.method:4} {check.path}")
            if not ok:
                failures += 1

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
