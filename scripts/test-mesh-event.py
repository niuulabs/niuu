#!/usr/bin/env python
"""Publish a test code.changed event to trigger the mesh cascade.

Usage:
    uv run scripts/test-mesh-event.py
"""

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sleipnir.adapters.discovery import ServiceRegistry
from sleipnir.adapters.nng_transport import NngTransport
from sleipnir.domain.events import SleipnirEvent


async def main() -> None:
    registry_path = Path("/tmp/ravn-mesh/sleipnir-registry.json")
    if not registry_path.exists():
        print("ERROR: Registry not found. Is the mesh running?")
        print("  Run: ./scripts/ravn-mesh.sh start")
        sys.exit(1)

    registry = ServiceRegistry(registry_path)
    transport = NngTransport(
        address="ipc:///tmp/ravn-mesh/test-pub.ipc",
        service_id="test-publisher",
        registry=registry,
    )

    print("Starting test publisher...")
    await transport.start()
    print("Waiting 10 seconds for mesh nodes to discover and dial...")
    await asyncio.sleep(10)

    correlation_id = f"test-{datetime.now(UTC).strftime('%H%M%S')}"
    event = SleipnirEvent(
        event_type="ravn.mesh.code.changed",
        source="ravn:test-publisher",
        payload={
            "ravn_event": {
                "event_type": "code.changed",
                "persona": "developer",
                "outcome": {
                    "file": "/tmp/hello.py",
                    "summary": "Test file with intentional bugs for review",
                    "changes": "Added initial code",
                },
            },
            "ravn_type": "outcome",
            "ravn_source": "ravn:test-publisher",
            "ravn_urgency": 0.8,
            "ravn_session_id": "",
            "ravn_task_id": f"test-task-{correlation_id}",
        },
        summary="Code changed: /tmp/hello.py needs review",
        urgency=0.8,
        domain="code",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id,
    )

    print(f"Publishing code.changed event (correlation_id={correlation_id})...")
    await transport.publish(event)
    print("Event published!")
    print()
    print("Watch the mesh logs:")
    print("  tail -f /tmp/ravn-mesh/ravn-mesh-*.log | grep -E '(task|outcome|skuld)'")

    print("Keeping publisher alive for 120s to allow mesh to process...")
    await asyncio.sleep(120)
    await transport.stop()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
