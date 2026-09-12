"""First-launch setup state: which wizard steps are done and what the host looks like.

The wizard is a front door over the platform's existing settings. This module
only records progress (so the web app knows whether to show the wizard) and
reports host facts and platform checks; every real configuration change goes
through the credentials, integrations and settings APIs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# Steps the wizard may record. Order is presentation order.
KNOWN_SETUP_STEPS: tuple[str, ...] = (
    "welcome",
    "system",
    "model",
    "providers",
    "git",
    "tracker",
    "runtime",
    "launch",
)


@dataclass(frozen=True)
class SetupStepRecord:
    """One completed wizard step with the non-secret choices it recorded."""

    step: str
    completed_at: datetime
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "completed_at": self.completed_at.isoformat(),
            "data": dict(self.data),
        }

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> SetupStepRecord:
        completed_at = datetime.fromisoformat(str(raw["completed_at"]))
        data = raw.get("data") or {}
        return SetupStepRecord(
            step=str(raw["step"]),
            completed_at=completed_at,
            data=dict(data) if isinstance(data, dict) else {},
        )


@dataclass(frozen=True)
class SetupState:
    """Persisted wizard progress."""

    completed: bool = False
    completed_at: datetime | None = None
    steps: dict[str, SetupStepRecord] = field(default_factory=dict)

    def with_step(self, record: SetupStepRecord) -> SetupState:
        steps = dict(self.steps)
        steps[record.step] = record
        return SetupState(completed=self.completed, completed_at=self.completed_at, steps=steps)

    def mark_completed(self, now: datetime | None = None) -> SetupState:
        return SetupState(
            completed=True,
            completed_at=now or datetime.now(UTC),
            steps=dict(self.steps),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "completed": self.completed,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "steps": {name: record.to_dict() for name, record in self.steps.items()},
        }

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> SetupState:
        completed_at_raw = raw.get("completed_at")
        steps_raw = raw.get("steps") or {}
        steps = {
            str(name): SetupStepRecord.from_dict(record)
            for name, record in steps_raw.items()
            if isinstance(record, dict)
        }
        return SetupState(
            completed=bool(raw.get("completed", False)),
            completed_at=(
                datetime.fromisoformat(str(completed_at_raw)) if completed_at_raw else None
            ),
            steps=steps,
        )


@dataclass(frozen=True)
class SystemCheck:
    """One platform-side check shown on the wizard's system step."""

    name: str
    passed: bool
    message: str
    warn_only: bool = False


@dataclass(frozen=True)
class SystemReport:
    """Host facts (written by `niuu up`) plus live platform checks."""

    host: dict[str, Any] | None
    checks: list[SystemCheck]

    @property
    def healthy(self) -> bool:
        return all(check.passed or check.warn_only for check in self.checks)
