"""Ports for live preview: discovering the session's listeners and admitting the router.

Adapters live in :mod:`skuld.preview_adapters`, selected by
``preview.port_scanner`` and ``preview.router_auth`` (dynamic adapters).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class ListeningPort:
    """A TCP port the session listens on, and the loopback address that reaches it.

    ``host`` is ``127.0.0.1`` when the listener accepts IPv4 on loopback, else
    ``::1``. Skuld only ever dials one of these two.
    """

    port: int
    host: str = "127.0.0.1"


class PortScanError(RuntimeError):
    """The listening sockets could not be read."""


class ListeningPortScanner(ABC):
    """Find the TCP ports processes of this session listen on."""

    @abstractmethod
    async def scan(self) -> set[ListeningPort]:
        """Return the session's listening ports, excluding Skuld's own.

        Raises:
            PortScanError: The socket table could not be read.
        """


class PreviewAuthError(Exception):
    """The request did not come from the platform's preview router."""


class PreviewRouterAuth(ABC):
    """Admit only the preview router to ``/preview/<port>/...``."""

    @abstractmethod
    async def verify(self, authorization: str, session_id: str) -> None:
        """Check the ``Authorization`` header of one preview request.

        Raises:
            PreviewAuthError: It is not a router credential for *session_id*.
        """
