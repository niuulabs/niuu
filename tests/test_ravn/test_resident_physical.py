from __future__ import annotations

import sys
from pathlib import Path

from ravn.adapters.physical.command import CommandPhysicalDeviceAdapter
from ravn.domain.physical_device import PhysicalActionKind, PhysicalActionRequest
from ravn.resident_continuation import LocalResidentMemory
from ravn.resident_physical import LocalResidentPhysicalMemory, ResidentPhysicalRuntime

MANDATE = (
    "A resident Ravn should safely inspect and improve a physical-world domain. "
    "Ask before spending money or operating physical machines."
)


def _adapter(tmp_path: Path) -> CommandPhysicalDeviceAdapter:
    marker = tmp_path / "marker.txt"
    artifact = tmp_path / "artifact.stl"
    artifact.write_text("solid cube\nendsolid cube\n", encoding="utf-8")
    return CommandPhysicalDeviceAdapter(
        timeout_seconds=5,
        max_output_bytes=4000,
        capabilities=[
            {
                "id": "telemetry",
                "name": "Telemetry",
                "description": "Read-only telemetry",
                "telemetry_command": [
                    sys.executable,
                    "-c",
                    "import json; print(json.dumps({'summary':'telemetry ok','value':42}))",
                ],
            },
            {
                "id": "dry-run",
                "name": "Dry Run",
                "description": "Dry-run artifact inspection",
                "dry_run_command": [
                    sys.executable,
                    "-c",
                    (
                        "import json, pathlib; "
                        f"p=pathlib.Path({str(artifact)!r}); "
                        "print(json.dumps({'summary':'dry-run ok','bytes':p.stat().st_size}))"
                    ),
                ],
            },
            {
                "id": "unsafe",
                "name": "Unsafe Operation",
                "description": "Must be gated",
                "risk_boundaries": ["physical_operation"],
                "execute_command": [
                    sys.executable,
                    "-c",
                    f"import pathlib; pathlib.Path({str(marker)!r}).write_text('executed')",
                ],
            },
        ],
    )


async def test_command_physical_adapter_lists_and_reads_real_telemetry(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)

    capabilities = await adapter.list_capabilities()
    result = await adapter.read_telemetry("telemetry")

    assert [capability.id for capability in capabilities] == ["telemetry", "dry-run", "unsafe"]
    assert result.status == "completed"
    assert result.telemetry["json"]["value"] == 42


async def test_resident_physical_runtime_persists_dry_run_reasoning(tmp_path: Path) -> None:
    runtime = ResidentPhysicalRuntime(
        device=_adapter(tmp_path),
        memory=LocalResidentPhysicalMemory(tmp_path / "memory"),
        continuation_memory=LocalResidentMemory(tmp_path / "memory"),
    )

    report = await runtime.dry_run(
        MANDATE,
        PhysicalActionRequest(
            capability_id="dry-run",
            action="Inspect artifact without operating a machine",
            kind=PhysicalActionKind.DRY_RUN.value,
            reason="Dry-run is safe and no-side-effect.",
        ),
    )
    refs = await LocalResidentPhysicalMemory(tmp_path / "memory").list_refs()

    assert report.results[0].status == "completed"
    assert "dry-run ok" in report.reasoning.summary
    assert any("/results/" in ref for ref in refs)
    assert any("/reasoning/" in ref for ref in refs)


async def test_physical_operation_is_blocked_before_command_executes(tmp_path: Path) -> None:
    marker = tmp_path / "marker.txt"
    runtime = ResidentPhysicalRuntime(
        device=_adapter(tmp_path),
        memory=LocalResidentPhysicalMemory(tmp_path / "memory"),
        continuation_memory=LocalResidentMemory(tmp_path / "memory"),
    )

    report = await runtime.execute(
        MANDATE,
        PhysicalActionRequest(
            capability_id="unsafe",
            action="Start printer",
            kind=PhysicalActionKind.PHYSICAL_OPERATION.value,
            reason="Would operate hardware.",
        ),
    )

    assert report.results[0].status == "blocked"
    assert report.results[0].approval_required is True
    assert not marker.exists()
    assert (tmp_path / "memory" / "resident/continuation/operator-needed/latest.md").exists()
    assert "Wait for operator approval" in report.reasoning.safe_next_action
