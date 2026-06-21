#!/usr/bin/env python3
"""Prove resident physical integration with real adapters and policy gates."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from niuu.utils import import_class, resolve_secret_kwargs
from ravn.config import Settings
from ravn.domain.physical_device import PhysicalActionKind, PhysicalActionRequest
from ravn.environment_signal_runtime import build_runtime_environment
from ravn.ports.physical_device import PhysicalDevicePort
from ravn.ports.signal_adapter import SignalAdapter
from ravn.resident_continuation import LocalResidentMemory
from ravn.resident_physical import (
    LocalResidentPhysicalMemory,
    ResidentPhysicalRuntime,
    ResidentPhysicalRuntimeConfig,
)

MANDATE = (
    "Kanuck Valley Models is my small 3D printing company.\n"
    "You are its resident Ravn.\n"
    "Help it become easier to run, more creative, and more successful.\n"
    "Ask before spending money or operating physical machines."
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default="", help="Proof workspace directory.")
    parser.add_argument("--config", default="", help="Ravn YAML config to load.")
    parser.add_argument("--mandate", default=MANDATE, help="Resident domain mandate.")
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    workspace = Path(args.workspace or Path.cwd() / ".resident-physical-proof").resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    os.chdir(workspace)

    config_path = Path(args.config).resolve() if args.config else _write_default_config(workspace)
    os.environ["RAVN_CONFIG"] = str(config_path)
    settings = Settings()
    memory_root = workspace / ".ravn"
    physical_memory = LocalResidentPhysicalMemory(memory_root)
    continuation_memory = LocalResidentMemory(memory_root)

    signal_count, signal_ref = await _collect_signal(settings, physical_memory)
    device = _build_device(settings)
    runtime = ResidentPhysicalRuntime(
        device=device,
        memory=physical_memory,
        continuation_memory=continuation_memory,
        config=ResidentPhysicalRuntimeConfig(proof_turn_index=1, max_reasoning_refs=8),
    )

    discover = await runtime.discover(args.mandate)
    telemetry = await runtime.read_telemetry(args.mandate, "proof-host-telemetry")
    dry_run = await runtime.dry_run(
        args.mandate,
        PhysicalActionRequest(
            capability_id="proof-stl-dry-run",
            action="Inspect generated STL metadata without slicing or printing",
            kind=PhysicalActionKind.DRY_RUN.value,
            reason="Validate a printable artifact path without operating a physical machine.",
        ),
    )
    marker = workspace / "unsafe-operation-marker.txt"
    blocked = await runtime.execute(
        args.mandate,
        PhysicalActionRequest(
            capability_id="proof-printer-operation",
            action="Start proof printer operation",
            kind=PhysicalActionKind.PHYSICAL_OPERATION.value,
            reason="Would operate a physical printer if approval were present.",
        ),
    )
    duplicate_audit_ref = await physical_memory.write_audit(
        "# Duplicate Path Audit\n\n"
        "- existing telemetry path: `SignalAdapter` via `PrinterPiSignalAdapter`\n"
        "- new action path: `PhysicalDevicePort` via configured physical adapter\n"
        "- canonical split: telemetry ingestion stays in environment signals; "
        "operations and dry-runs go through resident physical policy gates\n"
        f"- collected_signal_ref: {signal_ref}\n"
    )
    refs = await physical_memory.list_refs()
    operator_needed = memory_root / "resident/continuation/operator-needed/latest.md"
    policy_decisions = sorted(
        (memory_root / "resident/continuation/policy-decisions").glob("*.md")
    )

    if signal_count < 1:
        raise SystemExit("[proof] expected at least one real normalized signal")
    if not telemetry.results or telemetry.results[0].status != "completed":
        raise SystemExit("[proof] expected read-only telemetry to complete")
    if not dry_run.results or dry_run.results[0].status != "completed":
        raise SystemExit("[proof] expected dry-run to complete")
    if not blocked.results or not blocked.results[0].approval_required:
        raise SystemExit("[proof] expected unsafe physical operation to require approval")
    if marker.exists():
        raise SystemExit("[proof] unsafe physical operation executed despite approval gate")
    if not operator_needed.exists():
        raise SystemExit("[proof] expected existing resident operator-needed state")
    if not policy_decisions:
        raise SystemExit("[proof] expected persisted resident policy decision")
    if not any("reasoning" in ref for ref in refs):
        raise SystemExit("[proof] expected persisted resident reasoning")

    print("[proof] Resident physical integration proof.")
    print(f"[proof] workspace={workspace}")
    print(f"[proof] config={config_path}")
    print(f"[proof] memory={memory_root}")
    print(f"[proof] signal_count={signal_count}")
    print(f"[proof] signal_ref={signal_ref}")
    print(f"[proof] capabilities={len(discover.capabilities)}")
    print(f"[proof] telemetry_status={telemetry.results[0].status}")
    print(f"[proof] dry_run_status={dry_run.results[0].status}")
    print(f"[proof] blocked_status={blocked.results[0].status}")
    print(f"[proof] marker_exists={marker.exists()}")
    print(f"[proof] operator_needed={operator_needed}")
    print(f"[proof] policy_decisions={len(policy_decisions)}")
    print(f"[proof] duplicate_audit_ref={duplicate_audit_ref}")
    print(f"[proof] persisted_refs={len(refs)}")
    print(f"[proof] resident_reasoning={dry_run.reasoning.safe_next_action}")
    for ref in refs:
        print(f"[proof] ref={ref}")


async def _collect_signal(
    settings: Settings,
    memory: LocalResidentPhysicalMemory,
) -> tuple[int, str]:
    environment = build_runtime_environment(settings)
    count = 0
    summaries: list[str] = []
    for source in settings.environment.signal_sources:
        if not source.enabled:
            continue
        cls = import_class(source.adapter)
        kwargs = resolve_secret_kwargs(dict(source.kwargs), dict(source.secret_kwargs_env))
        kwargs.setdefault("environment", environment)
        kwargs.setdefault("source_id", source.id)
        adapter = cls(**kwargs)
        if not isinstance(adapter, SignalAdapter):
            raise TypeError(f"{source.adapter!r} does not implement SignalAdapter")
        signals = await adapter.collect()
        count += len(signals)
        summaries.extend(
            f"{signal.signal_type}:{signal.severity}:{signal.dedupe_key}" for signal in signals
        )
    ref = await memory.write_audit(
        "# Physical Telemetry Signal Collection\n\n"
        "Collected read-only environment telemetry through the existing signal adapter path.\n\n"
        + "\n".join(f"- {item}" for item in summaries)
        + "\n"
    )
    return count, ref


def _build_device(settings: Settings) -> PhysicalDevicePort:
    for device in settings.environment.physical_devices:
        if not device.enabled:
            continue
        cls = import_class(device.adapter)
        kwargs = resolve_secret_kwargs(dict(device.kwargs), dict(device.secret_kwargs_env))
        kwargs.setdefault("timeout_seconds", float(device.command_timeout_seconds))
        kwargs.setdefault("max_output_bytes", int(device.max_output_bytes))
        adapter = cls(**kwargs)
        if not isinstance(adapter, PhysicalDevicePort):
            raise TypeError(f"{device.adapter!r} does not implement PhysicalDevicePort")
        return adapter
    raise RuntimeError("No enabled environment.physical_devices adapter configured")


def _write_default_config(workspace: Path) -> Path:
    telemetry_file = workspace / "printer-telemetry.json"
    stl_file = workspace / "proof-cube.stl"
    marker = workspace / "unsafe-operation-marker.txt"
    telemetry_file.write_text(
        json.dumps(
            [
                {
                    "id": "proof-print-001",
                    "printer_id": "proof-printer",
                    "type": "telemetry",
                    "status": "idle",
                    "message": "Read-only proof telemetry observed.",
                    "filament_percent": 82,
                }
            ],
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    stl_file.write_text(
        "solid proof_cube\n"
        "facet normal 0 0 1\n"
        "outer loop\n"
        "vertex 0 0 0\n"
        "vertex 1 0 0\n"
        "vertex 0 1 0\n"
        "endloop\n"
        "endfacet\n"
        "endsolid proof_cube\n",
        encoding="utf-8",
    )
    config_path = workspace / "ravn-physical-proof.yaml"
    config = {
        "environment": {
            "id": "proof-printer-cell",
            "name": "Proof Printer Cell",
            "type": "printer.pi",
            "signal_sources": [
                {
                    "id": "proof-printer-telemetry",
                    "name": "Proof Printer Telemetry",
                    "kind": "printer_telemetry",
                    "adapter": "ravn.adapters.environment_signals.PrinterPiSignalAdapter",
                    "kwargs": {"raw_items_file": str(telemetry_file)},
                }
            ],
            "physical_devices": [
                {
                    "adapter": "ravn.adapters.physical.command.CommandPhysicalDeviceAdapter",
                    "command_timeout_seconds": 10,
                    "max_output_bytes": 8000,
                    "kwargs": {
                        "capabilities": [
                            {
                                "id": "proof-host-telemetry",
                                "name": "Proof Host Telemetry",
                                "description": (
                                    "Read-only local host telemetry used as a "
                                    "physical-world signal."
                                ),
                                "telemetry_command": [
                                    sys.executable,
                                    "-c",
                                    (
                                        "import json, platform; "
                                        "print(json.dumps({'summary':'host telemetry observed',"
                                        "'machine':platform.machine(),"
                                        "'platform':platform.platform()}))"
                                    ),
                                ],
                            },
                            {
                                "id": "proof-stl-dry-run",
                                "name": "Proof STL Dry Run",
                                "description": (
                                    "Inspect an STL artifact without slicing or "
                                    "operating a printer."
                                ),
                                "dry_run_command": [
                                    sys.executable,
                                    "-c",
                                    (
                                        "import json, pathlib; "
                                        f"p=pathlib.Path({str(stl_file)!r}); "
                                        "print(json.dumps({'summary':"
                                        "'stl dry-run inspection completed',"
                                        "'path':str(p),'bytes':p.stat().st_size,"
                                        "'solid':p.read_text().startswith('solid')}))"
                                    ),
                                ],
                            },
                            {
                                "id": "proof-printer-operation",
                                "name": "Proof Printer Operation",
                                "description": (
                                    "Unsafe proof operation that must be blocked "
                                    "before execution."
                                ),
                                "risk_boundaries": ["physical_operation"],
                                "execute_command": [
                                    sys.executable,
                                    "-c",
                                    (
                                        "import pathlib; "
                                        f"pathlib.Path({str(marker)!r}).write_text('executed')"
                                    ),
                                ],
                            },
                        ]
                    },
                }
            ],
        }
    }
    config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return config_path


if __name__ == "__main__":
    asyncio.run(_main())
