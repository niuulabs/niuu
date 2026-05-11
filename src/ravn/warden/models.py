"""Core models for persisted Ravn wardens."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


class WardenMimirBinding(BaseModel):
    """Mimir mount attachments for a warden."""

    mount_names: list[str] = Field(default_factory=list)
    write_mount: str = ""
    category_scope: list[str] = Field(default_factory=list)


class WardenFeatures(BaseModel):
    """Feature toggles for a long-lived autonomous warden."""

    wakefulness_enabled: bool = True
    dream_cycle_enabled: bool = True
    thread_queue_enabled: bool = True
    thread_enricher_enabled: bool = True
    recap_enabled: bool = True
    source_trigger_enabled: bool = True
    staleness_trigger_enabled: bool = True


class WardenDreamSummary(BaseModel):
    """Latest observed dream-cycle summary for a warden."""

    id: str = ""
    timestamp: datetime | None = None
    ravn: str = ""
    mounts: list[str] = Field(default_factory=list)
    pages_updated: int = 0
    entities_created: int = 0
    lint_fixes: int = 0
    duration_ms: int = 0


class WardenRuntime(BaseModel):
    """Persisted runtime status for a warden."""

    state: Literal["active", "idle", "offline"] = "offline"
    pages_touched: int = 0
    last_started_at: datetime | None = None
    last_dream: WardenDreamSummary | None = None


class WardenSupervisor(BaseModel):
    """Persisted deployment install metadata for a warden."""

    installed: bool = False
    service_label: str = ""
    service_file: str = ""
    config_file: str = ""
    start_command: str = ""
    last_install_at: datetime | None = None
    observation: "WardenObservation" = Field(default_factory=lambda: WardenObservation())


class WardenObservedField(BaseModel):
    """One backend-specific observed status field."""

    label: str = ""
    value: str = ""


class WardenObservation(BaseModel):
    """Most recent live observation gathered from the deployment backend."""

    status: Literal["running", "idle", "missing", "degraded", "unknown"] = "unknown"
    detail: str = ""
    source: str = ""
    checked_at: datetime | None = None
    fields: list[WardenObservedField] = Field(default_factory=list)


class WardenOperator(BaseModel):
    """Operator-facing presentation metadata for a warden."""

    rune: str = ""
    bio: str = ""
    expertise: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    role: str = ""


class WardenSpec(BaseModel):
    """Canonical persisted definition of a deployed Ravn warden."""

    id: str
    name: str
    persona: str = "research-and-distill"
    profile: str = ""
    deployment: str = "launchd"
    deployment_adapter: str = ""
    deployment_kwargs: dict[str, object] = Field(default_factory=dict)
    mimir: WardenMimirBinding = Field(default_factory=WardenMimirBinding)
    features: WardenFeatures = Field(default_factory=WardenFeatures)
    runtime: WardenRuntime = Field(default_factory=WardenRuntime)
    supervisor: WardenSupervisor = Field(default_factory=WardenSupervisor)
    operator: WardenOperator = Field(default_factory=WardenOperator)
    autostart: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    created_by: str = "operator"
