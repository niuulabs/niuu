"""Domain layer - models, ports, and services."""

from typing import TYPE_CHECKING

from volundr.domain.models import Session, SessionStatus
from volundr.domain.ports import PodManager, SessionRepository

if TYPE_CHECKING:
    from volundr.domain.services import SessionService

__all__ = [
    "Session",
    "SessionStatus",
    "SessionRepository",
    "PodManager",
    "SessionService",
]


def __getattr__(name: str):
    """Lazily import heavyweight service exports to avoid config import cycles."""
    if name == "SessionService":
        from volundr.domain.services import SessionService

        return SessionService
    raise AttributeError(name)
