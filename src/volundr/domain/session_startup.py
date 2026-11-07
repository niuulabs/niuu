"""What a session's launch is waiting on, stage by stage.

A launch walks through backend stages in order: a Kubernetes session pod is
scheduled, attaches its volumes, runs its init containers and starts its
containers; a VM session claims a machine, boots it, reaches the guest and
starts Skuld there. Each stage is the backend's own state with the time it
reported (or Forge observed) the change, never an estimate.

Stages are sequential: when a stage begins, every earlier stage still active
has finished at that moment. A backend may also name the stages it still has
to go through (``pending``), from what it knows about this launch (an init
container in the pod spec, a guest that is not reachable yet).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StartupStageState(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    DONE = "done"
    FAILED = "failed"


class StartupStage(BaseModel):
    """One step of a launch, as the backend reports it."""

    model_config = ConfigDict(frozen=True)

    key: str = Field(min_length=1, description="Stable stage id, e.g. 'schedule'")
    label: str = Field(min_length=1, description="What the launch is doing, for people")
    state: StartupStageState = StartupStageState.ACTIVE
    detail: str | None = Field(
        default=None,
        description="The backend's own words for this stage (event message, node, VM phase)",
    )
    started_at: datetime | None = Field(
        default=None, description="When the stage began; None while it is pending"
    )
    finished_at: datetime | None = None

    @model_validator(mode="after")
    def started_unless_pending(self) -> StartupStage:
        if self.state is not StartupStageState.PENDING and self.started_at is None:
            raise ValueError(f"Startup stage {self.key!r} is {self.state} without a start time")
        return self

    def ended(self, state: StartupStageState, at: datetime, detail: str | None) -> StartupStage:
        started = self.started_at or at
        return self.model_copy(
            update={
                "state": state,
                "detail": detail,
                "started_at": started,
                "finished_at": max(started, at),
            }
        )


class SessionStartup(BaseModel):
    """The stages of a session's latest launch."""

    model_config = ConfigDict(frozen=True)

    started_at: datetime = Field(description="When this launch began")
    handed_off_at: datetime | None = Field(
        default=None,
        description="When Forge's own pipeline handed the launch to the runtime backend",
    )
    stages: tuple[StartupStage, ...] = ()
    progress_error: str | None = Field(
        default=None,
        description="Why the backend's progress could not be read on the last attempt",
    )

    @classmethod
    def begin(cls, at: datetime, first: StartupStage) -> SessionStartup:
        return cls(started_at=at, stages=(first,))

    def handed_off(self, at: datetime) -> SessionStartup:
        return self.model_copy(update={"handed_off_at": at})

    def with_stage(self, stage: StartupStage) -> SessionStartup:
        """Record ``stage``: update the stage with its key, or add it in order.

        A stage keeps the earliest start it was ever reported with, so a backend
        that forgets an old step (an expired event) cannot move it later. A new
        stage that has begun goes before the stages still pending and ends every
        earlier stage still active; a new pending stage goes last.
        """
        stages = list(self.stages)
        index = next((i for i, s in enumerate(stages) if s.key == stage.key), None)
        if index is not None:
            current = stages[index]
            starts = [t for t in (current.started_at, stage.started_at) if t is not None]
            stages[index] = stage.model_copy(update={"started_at": min(starts, default=None)})
            if stage.started_at is not None and current.state is StartupStageState.PENDING:
                stages = self._close_before(stages, index, stage.started_at)
            return self.model_copy(update={"stages": tuple(stages)})
        if stage.state is StartupStageState.PENDING or stage.started_at is None:
            return self.model_copy(update={"stages": (*stages, stage)})
        position = next(
            (i for i, s in enumerate(stages) if s.state is StartupStageState.PENDING),
            len(stages),
        )
        stages.insert(position, stage)
        return self.model_copy(
            update={"stages": tuple(self._close_before(stages, position, stage.started_at))}
        )

    @staticmethod
    def _close_before(stages: list[StartupStage], index: int, at: datetime) -> list[StartupStage]:
        return [
            s.ended(StartupStageState.DONE, at, s.detail)
            if i < index and s.state is StartupStageState.ACTIVE
            else s
            for i, s in enumerate(stages)
        ]

    def with_stages(self, observed: Iterable[StartupStage]) -> SessionStartup:
        """Merge a backend's current view of its stages, in its order.

        The backend decides where its stages go relative to each other: a stage
        it only learns about later (a pod's volumes, once the pod exists) takes
        its place in the launch, not the end of the list. Stages outside the view
        (Forge's own, or the failure) keep their place around them.
        """
        view = list(observed)
        merged = self
        for stage in view:
            merged = merged.with_stage(stage)
        keys = [stage.key for stage in view]
        by_key = {stage.key: stage for stage in merged.stages}
        ordered: list[StartupStage] = []
        placed = False
        for stage in merged.stages:
            if stage.key not in by_key or stage.key not in keys:
                ordered.append(stage)
                continue
            if not placed:
                ordered.extend(by_key[key] for key in keys)
                placed = True
        return merged.model_copy(update={"stages": tuple(ordered), "progress_error": None})

    def with_progress_error(self, error: str) -> SessionStartup:
        return self.model_copy(update={"progress_error": error})

    def finished(self, at: datetime) -> SessionStartup:
        """The session is running: every stage still active ended at ``at``.

        Stages still pending never happened on the way, so they are dropped.
        """
        return self.model_copy(
            update={
                "stages": tuple(
                    s.ended(StartupStageState.DONE, at, s.detail)
                    if s.state is StartupStageState.ACTIVE
                    else s
                    for s in self.stages
                    if s.state is not StartupStageState.PENDING
                ),
                "progress_error": None,
            }
        )

    def failed(self, at: datetime, reason: str) -> SessionStartup:
        """The launch failed during its active stage, for ``reason``.

        With no stage active (the failure came after the last one finished), the
        failure is its own final stage so the reason is still shown in place.
        """
        stages = [s for s in self.stages if s.state is not StartupStageState.PENDING]
        active = [i for i, s in enumerate(stages) if s.state is StartupStageState.ACTIVE]
        if not active:
            stages.append(
                StartupStage(
                    key="failed",
                    label="Launch failed",
                    state=StartupStageState.FAILED,
                    detail=reason,
                    started_at=at,
                    finished_at=at,
                )
            )
            return self.model_copy(update={"stages": tuple(stages)})
        last = active[-1]
        return self.model_copy(
            update={
                "stages": tuple(
                    s.ended(StartupStageState.FAILED, at, reason)
                    if i == last
                    else s.ended(StartupStageState.DONE, at, s.detail)
                    if i in active
                    else s
                    for i, s in enumerate(stages)
                )
            }
        )

    @property
    def current(self) -> StartupStage | None:
        """The stage the launch is on now (the last one active or failed)."""
        for stage in reversed(self.stages):
            if stage.state in (StartupStageState.ACTIVE, StartupStageState.FAILED):
                return stage
        return None
