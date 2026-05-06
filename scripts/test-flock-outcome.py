#!/usr/bin/env python3
"""Publish a ravn.task.completed event to Tyr via Sleipnir webhook.

Simulates the event that a ravn flock coordinator publishes after completing
a raid. Use this to test the Tyr → ReviewEngine → auto-approve/retry/escalate
flow without running a full flock.

Usage:
    # Approve verdict (happy path)
    uv run scripts/test-flock-outcome.py \
        --tyr-url http://localhost:8081 \
        --session-id sess-001 \
        --verdict approve \
        --tests-passing true \
        --scope-adherence 0.95

    # Retry verdict
    uv run scripts/test-flock-outcome.py \
        --verdict retry --tests-passing false

    # Escalate verdict
    uv run scripts/test-flock-outcome.py \
        --verdict escalate --tests-passing false --scope-adherence 0.50

    # Custom session (must match a raid's session_id in Tyr)
    uv run scripts/test-flock-outcome.py \
        --session-id "volundr-session-abc123" \
        --verdict approve

Prerequisites:
    - Tyr running with ravn_outcome.enabled=true and sleipnir.enabled=true
    - A raid in RUNNING state with the matching session_id in Tyr's tracker
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import httpx


def build_event(
    session_id: str,
    verdict: str,
    tests_passing: bool | None,
    scope_adherence: float | None,
    summary: str,
    pr_url: str | None = None,
    files_changed: list[str] | None = None,
) -> dict:
    """Build a SleipnirEvent payload for ravn.task.completed."""
    payload: dict = {
        "verdict": verdict,
    }
    if tests_passing is not None:
        payload["tests_passing"] = tests_passing
    if scope_adherence is not None:
        payload["scope_adherence"] = scope_adherence
    if pr_url:
        payload["pr_url"] = pr_url
    if files_changed:
        payload["files_changed"] = files_changed
    if summary:
        payload["summary"] = summary

    return {
        "event_id": str(uuid4()),
        "event_type": "ravn.task.completed",
        "source": "ravn:coordinator",
        "payload": payload,
        "summary": f"Ravn task completed: verdict={verdict}",
        "urgency": 0.8,
        "domain": "code",
        "timestamp": datetime.now(UTC).isoformat(),
        "correlation_id": session_id,  # Must match raid.session_id in Tyr
        "causation_id": None,
        "tenant_id": None,
        "ttl": None,
    }


async def publish(tyr_url: str, event: dict) -> bool:
    """POST the event to Tyr's Sleipnir webhook endpoint."""
    endpoint = f"{tyr_url}/sleipnir/events"

    print(f"Publishing to: {endpoint}")
    print(f"Correlation ID (session_id): {event['correlation_id']}")
    print(f"Verdict: {event['payload']['verdict']}")
    print(f"Payload: {json.dumps(event['payload'], indent=2)}")
    print()

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                endpoint,
                json=event,
                headers={"Content-Type": "application/json"},
            )
            print(f"Response: HTTP {resp.status_code}")
            if resp.text:
                print(f"Body: {resp.text}")

            if resp.status_code in (200, 202, 204):
                print("\nSUCCESS: Event accepted by Tyr")
                return True

            print(f"\nWARNING: Unexpected status code {resp.status_code}")
            return False
    except httpx.ConnectError:
        print(f"\nERROR: Cannot connect to {tyr_url}")
        print("  Is Tyr running? Check: curl http://localhost:8081/api/v1/tyr/dispatcher")
        return False
    except Exception as e:
        print(f"\nERROR: {e}")
        return False


def parse_bool(val: str) -> bool | None:
    if val.lower() in ("true", "1", "yes"):
        return True
    if val.lower() in ("false", "0", "no"):
        return False
    if val.lower() in ("none", "null", "unknown"):
        return None
    raise argparse.ArgumentTypeError(f"Invalid boolean: {val}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Publish ravn.task.completed event to Tyr",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Happy path (approve)
  %(prog)s --verdict approve --tests-passing true --scope-adherence 0.95

  # Retry (failing tests)
  %(prog)s --verdict retry --tests-passing false

  # Escalate (low scope adherence)
  %(prog)s --verdict escalate --scope-adherence 0.40

  # With matching session ID
  %(prog)s --session-id volundr-sess-abc123 --verdict approve
""",
    )
    parser.add_argument(
        "--tyr-url",
        default="http://localhost:8080",
        help="Tyr base URL (default: http://localhost:8081)",
    )
    parser.add_argument(
        "--session-id",
        default=f"flock-e2e-{datetime.now(UTC).strftime('%H%M%S')}",
        help="Volundr session ID (must match a raid's session_id in Tyr)",
    )
    parser.add_argument(
        "--verdict",
        choices=["approve", "retry", "escalate"],
        default="approve",
        help="Coordinator verdict (default: approve)",
    )
    parser.add_argument(
        "--tests-passing",
        type=parse_bool,
        default=True,
        help="Whether tests pass (true/false/none)",
    )
    parser.add_argument(
        "--scope-adherence",
        type=float,
        default=None,
        help="Scope adherence score 0.0-1.0 (default: omitted)",
    )
    parser.add_argument(
        "--summary",
        default="E2E test outcome from test-flock-outcome.py",
        help="Summary text",
    )
    parser.add_argument(
        "--pr-url",
        default=None,
        help="PR URL (optional)",
    )

    args = parser.parse_args()

    event = build_event(
        session_id=args.session_id,
        verdict=args.verdict,
        tests_passing=args.tests_passing,
        scope_adherence=args.scope_adherence,
        summary=args.summary,
        pr_url=args.pr_url,
    )

    success = asyncio.run(publish(args.tyr_url, event))
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
