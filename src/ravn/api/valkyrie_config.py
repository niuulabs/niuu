"""Typed environment-catalog configuration for the Valkyrie dashboard."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from niuu.domain.models import InstanceVisibility
from ravn.api.valkyrie_projection_common import _field
from ravn.ports.resident_discovery import ResidentPlacement


class ValkyrieDashboardConfig(BaseSettings):
    """Catalog source for dashboard environments.

    The prefix preserves the legacy
    ``RAVN_VALKYRIE_DASHBOARD_ENVIRONMENTS_*`` variable names.
    """

    model_config = SettingsConfigDict(
        env_prefix="RAVN_VALKYRIE_DASHBOARD_",
        extra="ignore",
    )

    environments_json: str = ""
    environments_file: str = ""
    roster_stale_after_seconds: float = Field(
        default=900.0,
        gt=0,
        description=(
            "How recently a catalog Valkyrie must have been observed on telemetry to be "
            "listed as an active resident. Older (or never-observed) Valkyries are listed "
            "as idle. Keep this above the resident telemetry heartbeat, or a healthy "
            "resident flickers to idle between heartbeats."
        ),
    )

    @field_validator("environments_json")
    @classmethod
    def validate_environments_json(cls, value: str) -> str:
        if not value.strip():
            return value
        _parse_records(value, source="environments_json")
        return value


@dataclass(frozen=True)
class RosterFields:
    """Optional per-Valkyrie catalog fields that shape its resident-roster entry."""

    chat_endpoint: str | None
    visibility: InstanceVisibility
    owner_id: str
    tenant_id: str
    placement: ResidentPlacement | None


def roster_fields(record: dict[str, Any], *, source: str = "catalog") -> RosterFields:
    """Read and validate a catalog record's roster fields.

    Fields may sit on the ``valkyrie`` object or on the environment record
    itself, the same merge the dashboard projection applies. A malformed value
    raises instead of being dropped, so a typo cannot silently turn a chattable
    resident into an unreachable one.
    """
    valkyrie = _field(record, "valkyrie", default={})
    combined = {**record, **(valkyrie if isinstance(valkyrie, dict) else {})}
    label = f"{source}: {_field(combined, 'valkyrieId', 'valkyrie_id', 'id', 'name', default='?')}"
    return RosterFields(
        chat_endpoint=_chat_endpoint(_field(combined, "chatEndpoint", "chat_endpoint"), label),
        visibility=_visibility(_field(combined, "visibility", default="system"), label),
        owner_id=_text(_field(combined, "ownerId", "owner_id"), "ownerId", label),
        tenant_id=_text(_field(combined, "tenantId", "tenant_id"), "tenantId", label),
        placement=_placement(_field(combined, "placement", default=None), label),
    )


def _placement(value: Any, label: str) -> ResidentPlacement | None:
    """Where the Valkyrie runs: ``{cluster, namespace, workload}``, or absent."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(
            f"{label}: placement must be an object with cluster, namespace and workload, "
            f"got {type(value).__name__}"
        )
    try:
        return ResidentPlacement.model_validate(value)
    except ValidationError as exc:
        raise ValueError(
            f"{label}: placement {value!r} needs cluster, namespace and workload, each a "
            f"Kubernetes name: {exc}"
        ) from exc


def _chat_endpoint(value: Any, label: str) -> str | None:
    if value in ("", None):
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label}: chatEndpoint must be a string, got {type(value).__name__}")
    parts = urlsplit(value)
    if parts.scheme != "wss" or not parts.hostname or value != value.strip() or " " in value:
        raise ValueError(
            f"{label}: chatEndpoint {value!r} must be an absolute wss:// URL "
            "(for example wss://yggdrasil.niuu.world/rooms/<id>/ws/ravn); "
            "omit chatEndpoint for a resident without a chat room"
        )
    return value


def _visibility(value: Any, label: str) -> InstanceVisibility:
    try:
        return InstanceVisibility(value)
    except ValueError as exc:
        allowed = ", ".join(member.value for member in InstanceVisibility)
        raise ValueError(f"{label}: visibility {value!r} must be one of: {allowed}") from exc


def _text(value: Any, name: str, label: str) -> str:
    if value in ("", None):
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{label}: {name} must be a string, got {type(value).__name__}")
    return value.strip()


def _parse_records(raw: str, *, source: str) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid Valkyrie dashboard catalog JSON in {source}: {exc}") from exc
    records = parsed.get("environments") if isinstance(parsed, dict) else parsed
    if not isinstance(records, list):
        raise ValueError(
            f"Valkyrie dashboard catalog in {source} must be a list or object.environments"
        )
    parsed_records = [record for record in records if isinstance(record, dict)]
    for record in parsed_records:
        roster_fields(record, source=source)
    return parsed_records


def configured_environment_records(
    config: ValkyrieDashboardConfig,
) -> list[dict[str, Any]]:
    """Load and validate configured dashboard environment records."""
    raw = config.environments_json.strip()
    source = "environments_json"
    if not raw and config.environments_file.strip():
        path = Path(config.environments_file).expanduser()
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"cannot read Valkyrie dashboard catalog {path}: {exc}") from exc
        source = str(path)
    if not raw:
        return []
    return _parse_records(raw, source=source)
