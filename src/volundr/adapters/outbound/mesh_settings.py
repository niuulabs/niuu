"""Skuld's ``mesh`` settings for the sessions a pod manager launches.

Every pod manager that starts Skuld takes the same ``mesh`` kwarg (a mapping of Skuld's
``mesh`` settings, or its JSON text when it came through an environment variable) and hands
it to each session as ``SKULD__MESH__...`` variables, the way it hands over its other
session settings. Forge writes ``mesh.attestation.required`` into it, so the one decision
about mesh signing reaches every session whichever pod manager starts it
(:func:`volundr.composition_builders._apply_mesh_decision`).
"""

from __future__ import annotations

import json
from typing import Any

from volundr.domain.models import SessionSpec


def parse_mesh_settings(raw: dict | str | None, where: str = "pod_manager mesh") -> dict:
    """The ``mesh`` kwarg as a mapping; anything else is a configuration error."""
    if raw is None or raw == "":
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{where} must be a mapping of Skuld mesh settings, got {raw!r}: {exc}"
            ) from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{where} must be a mapping, got {type(raw).__name__}")
    return dict(raw)


def mesh_environment(mesh: dict, prefix: str = "SKULD__MESH") -> dict[str, str]:
    """Skuld's nested ``mesh`` settings as ``SKULD__MESH__...`` variables."""
    env: dict[str, str] = {}
    for key, value in mesh.items():
        name = f"{prefix}__{str(key).upper()}"
        if isinstance(value, dict) and value and all(isinstance(k, str) for k in value):
            env.update(mesh_environment(value, name))
            continue
        env[name] = _text(value)
    return env


def _text(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, dict | list):
        return json.dumps(value)
    return str(value)


def add_mesh_env_to_spec(spec: SessionSpec, mesh: dict) -> None:
    """Put the mesh variables into the spec's ``env``, where every runtime reads them."""
    if not mesh:
        return
    spec.values.setdefault("env", {}).update(mesh_environment(mesh))
