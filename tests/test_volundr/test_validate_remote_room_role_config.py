"""_validate_remote_room_role_config: fail fast at Forge startup.

pod_manager.room_role_source: remote is a whole-deployment decision — better
to refuse an impossible combination once at process startup than let it
render broken Kubernetes session pods one at a time.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from volundr.composition_builders import (
    _OPENBAO_SECRET_INJECTION_ADAPTER,
    _validate_remote_room_role_config,
)


def _settings(
    *,
    room_role_source="deployment",
    session_defaults=None,
    secret_injection_adapter=_OPENBAO_SECRET_INJECTION_ADAPTER,
):
    return SimpleNamespace(
        pod_manager=SimpleNamespace(
            room_role_source=room_role_source,
            kwargs={"session_defaults": session_defaults} if session_defaults else {},
        ),
        secret_injection=SimpleNamespace(adapter=secret_injection_adapter),
    )


def test_deployment_default_is_never_checked():
    """Not 'remote' at all — the whole validator is a no-op, regardless of
    how broken the rest of the config might be."""
    settings = _settings(
        room_role_source="deployment",
        session_defaults={"wsAuth": {"enforce_ownership": True}},
        secret_injection_adapter="something.else.Adapter",
    )
    _validate_remote_room_role_config(settings, "kubernetes")  # must not raise


def test_remote_on_a_non_kubernetes_backend_is_never_checked():
    settings = _settings(
        room_role_source="remote",
        session_defaults={"wsAuth": {"enforce_ownership": True}},
        secret_injection_adapter="something.else.Adapter",
    )
    _validate_remote_room_role_config(settings, "process")  # must not raise


def test_remote_with_a_valid_configuration_passes():
    settings = _settings(room_role_source="remote")
    _validate_remote_room_role_config(settings, "kubernetes")  # must not raise


def test_remote_with_enforce_ownership_in_session_defaults_raises():
    settings = _settings(
        room_role_source="remote",
        session_defaults={"wsAuth": {"enforce_ownership": True}},
    )
    with pytest.raises(ValueError, match="enforce_ownership"):
        _validate_remote_room_role_config(settings, "kubernetes")


def test_remote_with_enforce_ownership_false_in_session_defaults_passes():
    settings = _settings(
        room_role_source="remote",
        session_defaults={"wsAuth": {"enforce_ownership": False}},
    )
    _validate_remote_room_role_config(settings, "kubernetes")  # must not raise


def test_remote_without_openbao_secret_injection_raises():
    settings = _settings(room_role_source="remote", secret_injection_adapter="something.else")
    with pytest.raises(ValueError, match="secret_injection.adapter"):
        _validate_remote_room_role_config(settings, "kubernetes")
