"""Ports for rotating developer-execution coordinator credentials."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from uuid import UUID

from volundr.domain.models import PodSpecAdditions


@dataclass(frozen=True)
class DeveloperCredentialProjection:
    """Runtime-visible location and mount additions for one bearer token."""

    token_file: str
    pod_spec: PodSpecAdditions = PodSpecAdditions()


class DeveloperCredentialProjectionPort(ABC):
    """Projects a rotating bearer token into exactly one session runtime."""

    @abstractmethod
    def supports(self, runtime_backend: str) -> bool:
        """Return whether this projection supports *runtime_backend*."""

    @abstractmethod
    async def project(
        self,
        *,
        session_id: UUID,
        token: str,
        runtime_backend: str,
    ) -> DeveloperCredentialProjection:
        """Atomically publish *token* and return its runtime mount contract."""

    @abstractmethod
    async def remove(self, session_id: UUID) -> None:
        """Idempotently remove all projected material for *session_id*."""
