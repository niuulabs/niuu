"""Shared credential-broker behavior for Kubernetes-backed pod managers."""

from __future__ import annotations

import copy
import json

from volundr.domain.models import SessionSpec

DEFAULT_CODEX_AUTH_ADAPTER = "skuld.codex_auth.VolundrCodexAuthProvider"


class BrokeredCredentialPodManager:
    """Project one common Skuld broker contract across Kubernetes pod managers."""

    def _configure_brokered_credentials(
        self,
        *,
        codex_auth_adapter: str = DEFAULT_CODEX_AUTH_ADAPTER,
        codex_auth_kwargs: dict | None = None,
    ) -> None:
        self._codex_auth_adapter = codex_auth_adapter
        self._codex_auth_kwargs = dict(codex_auth_kwargs or {})

    def _with_brokered_credentials(self, spec: SessionSpec) -> SessionSpec:
        values = self._with_brokered_credential_values(spec.values)
        return SessionSpec(values=values, pod_spec=spec.pod_spec)

    def _with_brokered_credential_values(self, source: dict) -> dict:
        values = copy.deepcopy(source)
        broker = values.setdefault("broker", {})
        configured = broker.setdefault("codexAuth", {})
        configured.setdefault("adapter", self._codex_auth_adapter)
        kwargs = configured.setdefault("kwargs", {})
        defaults = copy.deepcopy(self._codex_auth_kwargs)
        defaults.update(kwargs)
        configured["kwargs"] = defaults
        return values

    @staticmethod
    def _brokered_credential_environment(spec: SessionSpec) -> dict[str, str]:
        return BrokeredCredentialPodManager._brokered_credential_environment_values(
            spec.values
        )

    @staticmethod
    def _brokered_credential_environment_values(values: dict) -> dict[str, str]:
        broker = values.get("broker")
        configured = broker.get("codexAuth") if isinstance(broker, dict) else None
        if not isinstance(configured, dict):
            return {}
        environment = {
            "SKULD__CODEX_AUTH__ADAPTER": str(configured.get("adapter") or ""),
            "SKULD__CODEX_AUTH__KWARGS": json.dumps(configured.get("kwargs") or {}),
        }
        return {name: value for name, value in environment.items() if value}
