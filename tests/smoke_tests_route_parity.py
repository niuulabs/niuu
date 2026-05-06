"""Smoke-test script for canonical vs legacy route parity.

Runs a focused set of HTTP calls against both legacy and canonical route
surfaces and reports whether responses are equivalent.

Usage:
    python -m tests/smoke_tests_route_parity --base-url http://localhost:8080 --token $TOKEN
    python -m tests/smoke_tests_route_parity --base-url http://localhost:8080 --domain forge
    python -m tests/smoke_tests_route_parity --base-url http://localhost:8080 --checklist

If no --base-url is given, the script reads from environment or defaults to
http://localhost:8080.

Options:
    --domain <name>   Filter to a specific domain (identity, tracker, integrations,
                      audit, forge, tokens, credentials, features, git, sessions).
    --checklist       Print a numbered checklist for manual execution.
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

# ---------------------------------------------------------------------------
# Route pair definitions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoutePair:
    """A legacy → canonical route pair for comparison."""

    name: str
    legacy: RouteSpec
    canonical: RouteSpec
    domain: str = ""
    priority: str = "high"  # high / medium / low


@dataclass(frozen=True)
class RouteSpec:
    path: str
    method: str = "GET"
    params: dict[str, Any] = field(default_factory=dict)
    json_body: Any = None
    headers: dict[str, str] = field(default_factory=dict)


# ── Representative route pairs per domain (from route-inventory.json) ────
# Only highest-risk domains from the NIU-768 raid scope.

ROUTE_PAIRS: list[RoutePair] = [
    # ══════════════════════════════════════════════════════════════════════
    # Identity
    # ══════════════════════════════════════════════════════════════════════
    RoutePair(
        "identity/me",
        RouteSpec("/api/v1/volundr/me", method="GET"),
        RouteSpec("/api/v1/identity/me", method="GET"),
        domain="identity",
        priority="high",
    ),
    RoutePair(
        "identity/tenants",
        RouteSpec("/api/v1/volundr/tenants", method="GET"),
        RouteSpec("/api/v1/identity/tenants", method="GET"),
        domain="identity",
        priority="high",
    ),
    RoutePair(
        "identity/settings",
        RouteSpec("/api/v1/volundr/settings", method="GET"),
        RouteSpec("/api/v1/identity/settings", method="GET"),
        domain="identity",
        priority="medium",
    ),
    RoutePair(
        "identity/users",
        RouteSpec("/api/v1/volundr/users", method="GET"),
        RouteSpec("/api/v1/identity/users", method="GET"),
        domain="identity",
        priority="medium",
    ),

    # ══════════════════════════════════════════════════════════════════════
    # Tracker
    # ══════════════════════════════════════════════════════════════════════
    RoutePair(
        "tracker/status",
        RouteSpec("/api/v1/volundr/tracker/status", method="GET"),
        RouteSpec("/api/v1/tracker/status", method="GET"),
        domain="tracker",
        priority="high",
    ),
    RoutePair(
        "tracker/repo-mappings",
        RouteSpec("/api/v1/volundr/tracker/mappings", method="GET"),
        RouteSpec("/api/v1/tracker/repo-mappings", method="GET"),
        domain="tracker",
        priority="high",
    ),
    RoutePair(
        "tracker/issues",
        RouteSpec("/api/v1/volundr/tracker/issues", method="GET", params={"q": "test"}),
        RouteSpec("/api/v1/tracker/issues", method="GET", params={"q": "test"}),
        domain="tracker",
        priority="high",
    ),

    # ══════════════════════════════════════════════════════════════════════
    # Integrations
    # ══════════════════════════════════════════════════════════════════════
    RoutePair(
        "integrations/list",
        RouteSpec("/api/v1/volundr/integrations", method="GET"),
        RouteSpec("/api/v1/integrations", method="GET"),
        domain="integrations",
        priority="high",
    ),

    # ══════════════════════════════════════════════════════════════════════
    # Audit
    # ══════════════════════════════════════════════════════════════════════
    RoutePair(
        "audit/events",
        RouteSpec("/api/v1/audit/events", method="GET", params={"limit": 10}),
        RouteSpec("/api/v1/audit/events", method="GET", params={"limit": 10}),
        domain="audit",
        priority="high",
    ),

    # ══════════════════════════════════════════════════════════════════════
    # Forge — Sessions
    # ══════════════════════════════════════════════════════════════════════
    RoutePair(
        "forge/sessions/list",
        RouteSpec("/api/v1/volundr/sessions", method="GET"),
        RouteSpec("/api/v1/forge/sessions", method="GET"),
        domain="forge",
        priority="high",
    ),
    RoutePair(
        "forge/chronicles",
        RouteSpec("/api/v1/volundr/chronicles", method="GET"),
        RouteSpec("/api/v1/forge/chronicles", method="GET"),
        domain="forge",
        priority="high",
    ),
    RoutePair(
        "forge/events",
        RouteSpec("/api/v1/volundr/events", method="GET"),
        RouteSpec("/api/v1/forge/events", method="GET"),
        domain="forge",
        priority="medium",
    ),

    # ══════════════════════════════════════════════════════════════════════
    # Forge — Catalog (templates, presets, profiles, resources, prompts)
    # ══════════════════════════════════════════════════════════════════════
    RoutePair(
        "forge/templates",
        RouteSpec("/api/v1/volundr/templates", method="GET"),
        RouteSpec("/api/v1/forge/templates", method="GET"),
        domain="forge",
        priority="high",
    ),
    RoutePair(
        "forge/presets",
        RouteSpec("/api/v1/volundr/presets", method="GET"),
        RouteSpec("/api/v1/forge/presets", method="GET"),
        domain="forge",
        priority="high",
    ),
    RoutePair(
        "forge/profiles",
        RouteSpec("/api/v1/volundr/profiles", method="GET"),
        RouteSpec("/api/v1/forge/profiles", method="GET"),
        domain="forge",
        priority="high",
    ),
    RoutePair(
        "forge/resources",
        RouteSpec("/api/v1/volundr/resources", method="GET"),
        RouteSpec("/api/v1/forge/resources", method="GET"),
        domain="forge",
        priority="medium",
    ),
    RoutePair(
        "forge/prompts",
        RouteSpec("/api/v1/volundr/prompts", method="GET"),
        RouteSpec("/api/v1/forge/prompts", method="GET"),
        domain="forge",
        priority="medium",
    ),
    RoutePair(
        "forge/mcp-servers",
        RouteSpec("/api/v1/volundr/mcp-servers", method="GET"),
        RouteSpec("/api/v1/forge/mcp-servers", method="GET"),
        domain="forge",
        priority="medium",
    ),

    # ══════════════════════════════════════════════════════════════════════
    # Forge — Models & Stats
    # ══════════════════════════════════════════════════════════════════════
    RoutePair(
        "forge/models",
        RouteSpec("/api/v1/volundr/models", method="GET"),
        RouteSpec("/api/v1/forge/models", method="GET"),
        domain="forge",
        priority="medium",
    ),
    RoutePair(
        "forge/stats",
        RouteSpec("/api/v1/volundr/stats", method="GET"),
        RouteSpec("/api/v1/forge/stats", method="GET"),
        domain="forge",
        priority="low",
    ),
    RoutePair(
        "forge/cluster",
        RouteSpec("/api/v1/volundr/cluster", method="GET"),
        RouteSpec("/api/v1/forge/cluster", method="GET"),
        domain="forge",
        priority="low",
    ),

    # ══════════════════════════════════════════════════════════════════════
    # Forge — Git
    # ══════════════════════════════════════════════════════════════════════
    RoutePair(
        "forge/repos/branches",
        RouteSpec(
            "/api/v1/volundr/repos/branches",
            method="GET",
            params={"repo_url": "github.com/acme/repo"},
        ),
        RouteSpec(
            "/api/v1/niuu/repos/branches",
            method="GET",
            params={"repo_url": "github.com/acme/repo"},
        ),
        domain="forge",
        priority="high",
    ),
    RoutePair(
        "forge/repos/prs",
        RouteSpec(
            "/api/v1/volundr/repos/prs",
            method="GET",
            params={"repo_url": "github.com/acme/repo"},
        ),
        RouteSpec(
            "/api/v1/forge/repos/prs",
            method="GET",
            params={"repo_url": "github.com/acme/repo"},
        ),
        domain="forge",
        priority="high",
    ),
    RoutePair(
        "forge/git",
        RouteSpec("/api/v1/volundr/git", method="GET"),
        RouteSpec("/api/v1/forge/git", method="GET"),
        domain="forge",
        priority="medium",
    ),

    # ══════════════════════════════════════════════════════════════════════
    # Forge — Workspaces
    # ══════════════════════════════════════════════════════════════════════
    RoutePair(
        "forge/workspaces",
        RouteSpec("/api/v1/volundr/workspaces", method="GET"),
        RouteSpec("/api/v1/forge/workspaces", method="GET"),
        domain="forge",
        priority="medium",
    ),

    # ══════════════════════════════════════════════════════════════════════
    # Tokens (PATs)
    # ══════════════════════════════════════════════════════════════════════
    RoutePair(
        "tokens/list",
        RouteSpec("/api/v1/volundr/tokens", method="GET"),
        RouteSpec("/api/v1/tokens", method="GET"),
        domain="tokens",
        priority="high",
    ),
    RoutePair(
        "tokens/users/list",
        RouteSpec("/api/v1/users/tokens", method="GET"),
        RouteSpec("/api/v1/tokens", method="GET"),
        domain="tokens",
        priority="medium",
    ),

    # ══════════════════════════════════════════════════════════════════════
    # Credentials & Secrets
    # ══════════════════════════════════════════════════════════════════════
    RoutePair(
        "credentials/user",
        RouteSpec("/api/v1/volundr/credentials", method="GET"),
        RouteSpec("/api/v1/credentials", method="GET"),
        domain="credentials",
        priority="high",
    ),
    RoutePair(
        "credentials/secrets",
        RouteSpec("/api/v1/volundr/secrets", method="GET"),
        RouteSpec("/api/v1/credentials/secrets", method="GET"),
        domain="credentials",
        priority="high",
    ),
    RoutePair(
        "credentials/mcp-servers",
        RouteSpec("/api/v1/volundr/mcp-servers", method="GET"),
        RouteSpec("/api/v1/credentials/mcp-servers", method="GET"),
        domain="credentials",
        priority="low",
    ),

    # ══════════════════════════════════════════════════════════════════════
    # Features
    # ══════════════════════════════════════════════════════════════════════
    RoutePair(
        "features/modules",
        RouteSpec("/api/v1/volundr/features", method="GET"),
        RouteSpec("/api/v1/features", method="GET"),
        domain="features",
        priority="medium",
    ),
]


# Fields to ignore when comparing JSON payloads (internal-only fields)
IGNORED_FIELDS = frozenset(
    {
        "chat_endpoint",
        "code_endpoint",
        "pod_name",
        "error",
        "archived_at",
    }
)


def _strip_internal(payload: Any) -> Any:
    """Remove internal-only fields from a JSON payload for comparison."""
    if isinstance(payload, dict):
        return {k: _strip_internal(v) for k, v in payload.items() if k not in IGNORED_FIELDS}
    if isinstance(payload, list):
        return [_strip_internal(item) for item in payload]
    return payload


def _same_shape(resp_a: httpx.Response, resp_b: httpx.Response) -> tuple[bool, str]:
    """Compare two responses — check status code then JSON shape."""
    if resp_a.status_code != resp_b.status_code:
        return False, (
            f"status mismatch: {resp_a.status_code} vs {resp_b.status_code}"
        )

    # Allow 204 No Content without body
    if resp_a.status_code == 204 and resp_b.status_code == 204:
        return True, "OK (204)"

    try:
        payload_a = resp_a.json()
    except Exception:
        payload_a = None

    try:
        payload_b = resp_b.json()
    except Exception:
        payload_b = None

    if payload_a is None and payload_b is None:
        return True, "OK (no body)"

    if payload_a is None or payload_b is None:
        return False, "body mismatch (one is None)"

    normalized_a = _strip_internal(payload_a)
    normalized_b = _strip_internal(payload_b)

    if normalized_a == normalized_b:
        return True, "OK"

    # Fuzzy diff: show top-level keys that differ
    set_a = set(normalized_a.keys()) if isinstance(normalized_a, dict) else set()
    set_b = set(normalized_b.keys()) if isinstance(normalized_b, dict) else set()
    if set_a != set_b:
        return False, f"keys differ: {set_a.symmetric_difference(set_b)}"

    return False, "payload differs (see --verbose for details)"


def run_smoke_test(
    base_url: str,
    token: str | None = None,
    timeout: float = 10.0,
    domain: str | None = None,
) -> list[tuple[RoutePair, bool, str]]:
    """Run all smoke tests and return results."""
    results: list[tuple[RoutePair, bool, str]] = []
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    pairs = ROUTE_PAIRS
    if domain:
        pairs = [p for p in ROUTE_PAIRS if p.domain == domain]
        log.info("Filtering to domain: %s (%d pairs)", domain, len(pairs))

    with httpx.Client(base_url=base_url, timeout=timeout, headers=headers) as client:
        for pair in pairs:
            log.info("[%s] Testing %s …", pair.domain.upper(), pair.name)
            try:
                legacy_resp = client.request(
                    pair.legacy.method,
                    pair.legacy.path,
                    params=pair.legacy.params,
                    json=pair.legacy.json_body,
                )
                canonical_resp = client.request(
                    pair.canonical.method,
                    pair.canonical.path,
                    params=pair.canonical.params,
                    json=pair.canonical.json_body,
                )
            except httpx.RequestError as exc:
                results.append((pair, False, f"request error: {exc}"))
                continue

            ok, detail = _same_shape(legacy_resp, canonical_resp)
            results.append((pair, ok, detail))
            status = "PASS" if ok else "FAIL"
            log.info("  %s: %s — %s", status, pair.name, detail)

    return results


def print_report(results: list[tuple[RoutePair, bool, str]]) -> int:
    """Print a summary report. Returns 1 if any test failed."""
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    failed = total - passed

    print()
    print("=" * 70)
    print("  Smoke Test Report: Canonical Route Parity")
    print("=" * 70)
    print(f"  Total : {total}")
    print(f"  Passed: {passed}")
    print(f"  Failed: {failed}")
    print("=" * 70)

    # Group by domain for better visibility
    by_domain: dict[str, list[tuple[RoutePair, bool, str]]] = {}
    for pair, ok, detail in results:
        by_domain.setdefault(pair.domain, []).append((pair, ok, detail))

    if by_domain:
        print()
        print("By domain:")
        for domain in sorted(by_domain):
            domain_results = by_domain[domain]
            d_passed = sum(1 for _, ok, _ in domain_results if ok)
            d_total = len(domain_results)
            print(f"  {domain:>15}: {d_passed}/{d_total}")

    if failed:
        print()
        print("Failures:")
        for pair, ok, detail in results:
            if not ok:
                print(f"  ✗ [{pair.domain.upper()}] {pair.name}: {detail}")

    print()
    return 1 if failed else 0


def print_checklist(results: list[tuple[RoutePair, bool, str]]) -> None:
    """Print a numbered checklist for manual execution."""
    print()
    print("=" * 70)
    print("  Smoke Test Checklist — Manual Execution")
    print("  NIU-768: Canonical route parity confidence pass")
    print("=" * 70)
    print()
    print("Instructions:")
    print("  1. Start volundr/niuu with both legacy and canonical routes active.")
    print("  2. For each item below, verify both routes return equivalent responses.")
    print("  3. Mark [x] when passed, [ ] when skipped or failing.")
    print("  4. Note any differences in the 'observed' column.")
    print()
    print("-" * 70)
    print(f"  {'#':<4} {'Domain':<15} {'Priority':<10} {'Legacy path':<42} Canonical path")
    print("-" * 70)

    for i, (pair, ok, detail) in enumerate(results, 1):
        legacy_path = f"{pair.legacy.path}"
        canonical_path = f"{pair.canonical.path}"
        if pair.legacy.params:
            legacy_path += "?" + "&".join(
                f"{k}={v}" for k, v in pair.legacy.params.items()
            )
        if pair.canonical.params:
            canonical_path += "?" + "&".join(
                f"{k}={v}" for k, v in pair.canonical.params.items()
            )
        marker = "[x]" if ok else "[ ]"
        priority_tag = pair.priority.upper()
        print(
            f"  {marker} {i:<3} {pair.domain:<15} {priority_tag:<10} "
            f"{legacy_path:<42} {canonical_path}"
        )

    print()
    print("Results from automated run:")
    failed = [(p, d) for p, ok, d in results if not ok]
    if failed:
        for pair, detail in failed:
            print(f"  ✗ {pair.name}: {detail}")
    else:
        print("  All checks passed!")

    print()
    print("-" * 70)
    print("  Status: ___________    Sign-off: ___________    Date: ___________")
    print("=" * 70)


def print_checklist_prompt() -> None:
    """Print a checklist template for users to fill in."""
    print()
    print("=" * 70)
    print("  Smoke Test Checklist Template — Manual Execution")
    print("  NIU-768: Canonical route parity confidence pass")
    print("=" * 70)
    print()
    print("Instructions:")
    print("  1. Start volundr/niuu with both legacy and canonical routes active.")
    print("  2. For each item below, verify both routes return equivalent responses.")
    print("  3. Mark [x] when passed, [ ] when skipped or failing.")
    print("  4. Note any differences in the 'observed' column.")
    print()
    print("-" * 70)
    print(f"  {'#':<4} {'Domain':<15} {'Priority':<10} {'Legacy path':<42} Canonical path")
    print("-" * 70)

    for i, pair in enumerate(ROUTE_PAIRS, 1):
        legacy_path = pair.legacy.path
        if pair.legacy.params:
            legacy_path += "?" + "&".join(
                f"{k}={v}" for k, v in pair.legacy.params.items()
            )
        canonical_path = pair.canonical.path
        if pair.canonical.params:
            canonical_path += "?" + "&".join(
                f"{k}={v}" for k, v in pair.canonical.params.items()
            )
        priority_tag = pair.priority.upper()
        print(
            f"  [ ] {i:<3} {pair.domain:<15} {priority_tag:<10} "
            f"{legacy_path:<42} {canonical_path}"
        )

    print()
    print("-" * 70)
    print("  Status: ___________    Sign-off: ___________    Date: ___________")
    print("=" * 70)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smoke-test canonical vs legacy route parity.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Base URL of the volundr/niuu instance (default: http://localhost:8080).",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="Bearer token for authentication.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Request timeout in seconds (default: 10).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print full JSON diffs on failure.",
    )
    parser.add_argument(
        "--domain",
        default=None,
        choices=[
            "identity", "tracker", "integrations", "audit",
            "forge", "tokens", "credentials", "features",
        ],
        help="Filter to a specific domain for focused testing.",
    )
    parser.add_argument(
        "--checklist",
        action="store_true",
        help="Print a numbered checklist for manual execution.",
    )

    args = parser.parse_args()

    # Strip trailing slash from base URL if provided
    base_url = args.base_url
    if base_url:
        base_url = base_url.rstrip("/")

    if not base_url and args.checklist:
        # No server provided, just show template
        print_checklist_prompt()
        sys.exit(0)

    if not base_url:
        base_url = "http://localhost:8080"

    results = run_smoke_test(base_url, args.token or None, timeout=args.timeout, domain=args.domain)
    print_report(results)

    if args.checklist:
        print_checklist(results)

    sys.exit(1 if any(not ok for _, ok, _ in results) else 0)


if __name__ == "__main__":
    main()
