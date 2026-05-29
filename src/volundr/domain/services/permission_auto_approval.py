"""Server-side evaluation for permission auto approval policy."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any, Literal

from volundr.config import PermissionAutoApprovalConfig

AutoApprovalReason = Literal[
    "allowed",
    "disabled",
    "no_command",
    "denylist",
    "no_allowlist_match",
]


@dataclass(frozen=True)
class AutoApprovalEvaluation:
    """Result of checking one permission request against server policy."""

    can_auto_approve: bool
    reason: AutoApprovalReason
    command: str | None
    delay_seconds: int
    matched_pattern: str | None = None


def extract_permission_command(
    *,
    command: str | None = None,
    input: dict[str, Any] | None = None,
) -> str | None:
    """Extract a shell command from a permission request payload.

    The policy intentionally evaluates structured command fields only. Free-form
    descriptions are display text and should not become an approval source.
    """
    if isinstance(command, str) and command.strip():
        return command.strip()

    payload = input or {}
    for key in ("command", "cmd", "shell_command"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def normalize_permission_command(command: str) -> str:
    """Normalize common shell wrappers before matching policy regexes."""
    current = command.strip()
    for _ in range(3):
        try:
            parts = shlex.split(current)
        except ValueError:
            return current

        if len(parts) < 3:
            return current

        shell_name = PurePath(parts[0]).name
        if shell_name not in {"bash", "sh", "zsh"}:
            return current

        option = parts[1]
        if "c" not in option or not option.startswith("-"):
            return current

        next_command = parts[2].strip()
        if not next_command or next_command == current:
            return current
        current = next_command

    return current


def pattern_matches(command: str, pattern: str) -> bool:
    """Return whether a configured regex pattern matches a command."""
    normalized = pattern.strip()
    if not normalized:
        return False
    try:
        return re.search(normalized, command) is not None
    except re.error:
        return normalized in command


def evaluate_permission_auto_approval(
    *,
    command: str | None = None,
    input: dict[str, Any] | None = None,
    policy: PermissionAutoApprovalConfig,
) -> AutoApprovalEvaluation:
    """Evaluate a permission request against Volundr's configured policy."""
    extracted_command = extract_permission_command(command=command, input=input)
    delay_seconds = policy.delay_seconds

    if not policy.enabled:
        return AutoApprovalEvaluation(False, "disabled", extracted_command, delay_seconds)
    if not extracted_command:
        return AutoApprovalEvaluation(False, "no_command", None, delay_seconds)

    normalized_command = normalize_permission_command(extracted_command)
    commands_to_check = [extracted_command]
    if normalized_command != extracted_command:
        commands_to_check.append(normalized_command)

    deny_pattern = next(
        (
            pattern
            for pattern in policy.denylist
            if any(pattern_matches(command, pattern) for command in commands_to_check)
        ),
        None,
    )
    if deny_pattern:
        return AutoApprovalEvaluation(
            False,
            "denylist",
            normalized_command,
            delay_seconds,
            deny_pattern,
        )

    allow_pattern = next(
        (
            pattern
            for pattern in policy.allowlist
            if any(pattern_matches(command, pattern) for command in commands_to_check)
        ),
        None,
    )
    if not allow_pattern:
        return AutoApprovalEvaluation(
            False,
            "no_allowlist_match",
            normalized_command,
            delay_seconds,
        )

    return AutoApprovalEvaluation(
        True,
        "allowed",
        normalized_command,
        delay_seconds,
        allow_pattern,
    )
