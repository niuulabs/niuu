"""PeopleDirectory — what Forge says about a person, asked with the person's own login.

The people routes of the memory gateway (contract C9) decide who may approve,
reject and promote. Forge holds the facts they need: a person's role on a
project (C5), a session's owner, a workflow run's owner (C6). Every question is
asked with the person's own credential passed through, so Forge's visibility
rules hold: a project or session the person cannot see reads as not found.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class Person:
    """A person calling the gateway, as Mímir's identity adapter verified them.

    ``authorization`` is their ``Authorization`` header, passed to Forge as is;
    empty in mini mode without auth, where Forge asks for none.
    """

    user_id: str
    tenant_id: str
    roles: tuple[str, ...]
    authorization: str = ""

    @property
    def ref(self) -> str:
        return f"person:{self.user_id}"


@dataclass(frozen=True)
class Owned:
    """Who owns a session or a run, and in which org."""

    owner_id: str
    tenant_id: str
    session_ids: tuple[str, ...] = ()


class NotVisibleError(Exception):
    """Forge answered 404: the thing does not exist, or this person may not know it does."""


class PersonRefusedError(Exception):
    """Forge refused the person's login (401/403). ``status`` is Forge's answer."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class ForgeUnavailableError(Exception):
    """Forge could not be asked, or answered something unusable."""


class PeopleDirectory(ABC):
    @abstractmethod
    async def project_role(self, project_id: str, person: Person) -> str:
        """``viewer``, ``contributor``, ``approver`` or ``owner`` (a tenant admin is owner)."""

    @abstractmethod
    async def session_owner(self, session_id: str, person: Person) -> Owned:
        """The session's owner and org."""

    @abstractmethod
    async def run_owner(self, run_id: str, person: Person) -> Owned:
        """The workflow run's owner, org and sessions."""

    async def close(self) -> None:  # noqa: B027 — optional hook
        """Release connections."""
