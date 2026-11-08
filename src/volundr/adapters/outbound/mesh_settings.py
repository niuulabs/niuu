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
from collections.abc import Mapping
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


def mesh_is_nng(mesh: Mapping[str, Any]) -> bool:
    """Whether the sessions run a mesh on the nng transport (Skuld's default transport)."""
    enabled = mesh.get("enabled")
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() == "true"
    return bool(enabled) and str(mesh.get("transport") or "nng") == "nng"


def mesh_with_defaults(mesh: dict, defaults: Mapping[str, Any]) -> dict:
    """Fill in what the platform knows about this shape, for a mesh the operator enabled.

    ``mesh.enabled: true`` on nng is all the operator has to say: the pod manager that
    starts the sessions knows where they run (one host, or a cluster) and so how they find
    each other. Whatever the operator set stays as set: a key of ``defaults`` is added only
    when the mesh has none, a mapping fills in only its missing keys, and
    ``discovery_adapters`` is left alone when any discovery (``adapters`` too) is configured.
    A mesh that is off, or on another transport (NATS has its own), is returned unchanged.
    """
    if not mesh_is_nng(mesh):
        return mesh
    merged = dict(mesh)
    for key, value in defaults.items():
        if key == "discovery_adapters" and (mesh.get("discovery_adapters") or mesh.get("adapters")):
            continue
        if isinstance(value, Mapping):
            merged[key] = {**value, **dict(mesh.get(key) or {})}
            continue
        merged.setdefault(key, value)
    return merged


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
