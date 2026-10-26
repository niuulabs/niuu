from __future__ import annotations

import pytest

from ravn.domain.checkpoint import InterruptReason
from ravn.domain.harness import (
    HarnessCapabilityProfile,
    HarnessContractError,
    HarnessControl,
    HarnessControlKind,
    HarnessKind,
    SteeringMode,
)


def test_native_and_cli_harness_profiles_are_representable_without_runtime_types() -> None:
    native = HarnessCapabilityProfile.native_ravn_agent()
    cli = HarnessCapabilityProfile.cli_backed(
        supports_reasoning=True,
        supports_attachments=True,
        supports_images=True,
        supports_interrupt=True,
        steering_mode=SteeringMode.INTERRUPT_RESUME,
        supports_session_resume=True,
    )

    assert native.kind is HarnessKind.NATIVE
    assert native.supports_dynamic_tools is True
    assert native.supports_steering is False
    assert cli.kind is HarnessKind.CLI_BACKED
    assert cli.supports_images is True
    assert cli.supports_session_resume is True
    assert cli.supports_steering is True


@pytest.mark.parametrize(
    ("profile", "message"),
    [
        (
            {
                "supports_images": True,
            },
            "image input requires attachment input support",
        ),
        (
            {
                "supports_dynamic_tools": True,
            },
            "dynamic tool exposure requires tool execution support",
        ),
        (
            {
                "supports_interrupt": False,
                "steering_mode": SteeringMode.INTERRUPT_RESUME,
            },
            "interrupt-resume steering requires interrupt support",
        ),
        (
            {
                "steering_mode": "interrupt_resume",
            },
            "steering mode must be a SteeringMode",
        ),
    ],
)
def test_invalid_harness_capability_profiles_fail_deterministically(
    profile: dict[str, object], message: str
) -> None:
    defaults: dict[str, object] = {
        "kind": HarnessKind.CLI_BACKED,
        "supports_tools": False,
        "supports_reasoning": False,
        "supports_attachments": False,
        "supports_images": False,
        "supports_interrupt": False,
        "steering_mode": SteeringMode.NONE,
        "supports_session_resume": False,
        "supports_dynamic_tools": False,
    }

    with pytest.raises(HarnessContractError, match=message):
        HarnessCapabilityProfile(**(defaults | profile))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "control",
    [
        HarnessControl(HarnessControlKind.INTERRUPT, interrupt_reason=InterruptReason.SIGINT),
        HarnessControl(HarnessControlKind.STEER, content="use the safer plan"),
    ],
)
def test_harness_controls_represent_supported_control_requests(control: HarnessControl) -> None:
    assert control.kind in {HarnessControlKind.INTERRUPT, HarnessControlKind.STEER}


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {"kind": HarnessControlKind.INTERRUPT},
            "interrupt control requires an interrupt reason",
        ),
        (
            {"kind": HarnessControlKind.STEER},
            "steering control requires nonblank content",
        ),
    ],
)
def test_invalid_harness_controls_fail_deterministically(
    kwargs: dict[str, object], message: str
) -> None:
    with pytest.raises(HarnessContractError, match=message):
        HarnessControl(**kwargs)  # type: ignore[arg-type]


def test_ravn_domain_and_ports_do_not_import_skuld_implementations() -> None:
    import ast
    from pathlib import Path

    source_root = Path(__file__).resolve().parents[2] / "src" / "ravn"
    violations: list[str] = []
    for directory in (source_root / "domain", source_root / "ports"):
        for path in directory.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported = [node.module]
                else:
                    continue
                if any(name == "skuld" or name.startswith("skuld.") for name in imported):
                    violations.append(str(path.relative_to(source_root)))

    assert violations == []
