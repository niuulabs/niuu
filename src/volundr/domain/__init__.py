"""Domain layer - models, ports, and services."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from volundr.domain.models import Session, SessionStatus
    from volundr.domain.ports import PodManager, SessionRepository
    from volundr.domain.services import SessionService

__all__ = [
    "Session",
    "SessionStatus",
    "SessionRepository",
    "PodManager",
    "SessionService",
]


def __getattr__(name: str):
    """Load public exports only when requested, keeping compute configuration lightweight."""
    if name in {"Session", "SessionStatus"}:
        from volundr.domain import models

        return getattr(models, name)
    if name in {"PodManager", "SessionRepository"}:
        from volundr.domain import ports

        return getattr(ports, name)
    if name == "SessionService":
        from volundr.domain.services import SessionService

        return SessionService
    raise AttributeError(name)
