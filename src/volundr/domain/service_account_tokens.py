"""Service-account identity for session workloads that Kubernetes does not run.

A session pod gets its ServiceAccount's tokens projected by the kubelet. A VM
session has no kubelet, so the controller requests the same audience-bound,
expiring tokens and delivers them into the guest itself.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


class ServiceAccountTokenError(RuntimeError):
    """A token for a session's ServiceAccount could not be issued."""


@dataclass(frozen=True)
class ServiceAccountToken:
    token: str
    expires_at: datetime

    def __repr__(self) -> str:
        return f"ServiceAccountToken(expires_at={self.expires_at.isoformat()})"


class ServiceAccountTokenPort(ABC):
    """Issue the tokens a kubelet would project for a ServiceAccount."""

    @abstractmethod
    async def issue(
        self,
        service_account: str,
        *,
        audience: str,
        expiration_seconds: int,
    ) -> ServiceAccountToken:
        """Return a token bound to ``audience``; raise ``ServiceAccountTokenError``."""

    async def close(self) -> None:
        """Release client resources."""
