"""Typed environment-catalog configuration for the Valkyrie dashboard."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    @field_validator("environments_json")
    @classmethod
    def validate_environments_json(cls, value: str) -> str:
        if not value.strip():
            return value
        _parse_records(value, source="environments_json")
        return value


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
    return [record for record in records if isinstance(record, dict)]


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
