"""ProposalNotifier — telling a layer's deciders that a change waits for them.

Forge owns notifications (its feed, delivery rules and sinks), so the gateway does not
keep a second channel: when an agent's write becomes a proposal to a **project** layer,
the gateway asks Forge, with the proposing session's own credential, to tell the
project's approvers and owners; when a person decides the proposal, the gateway asks
Forge, with that person's own login, to settle what it told them.

Org-layer proposals are not announced: Forge cannot list an org's admins (the identity
provider holds them), so org admins find them on the Review page.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class ProposalNotifyError(Exception):
    """Forge could not be told. The message names why."""


@dataclass(frozen=True)
class WaitingProposal:
    """What Forge needs to tell a project's deciders about one proposal."""

    proposal_id: str
    project_id: str
    title: str
    why: str


class ProposalNotifier(ABC):
    @abstractmethod
    async def waiting(self, session_id: str, proposal: WaitingProposal, *, credential: str) -> None:
        """Tell the project's approvers and owners that *proposal* waits for them.

        Made with the proposing session's own ``forge_session`` credential.
        """

    @abstractmethod
    async def decided(
        self, project_id: str, proposal_id: str, *, authorization: str, outcome: str
    ) -> None:
        """Settle what the deciders were told: *outcome* says how it was decided.

        Made with the deciding person's own ``Authorization`` header.
        """

    async def close(self) -> None:  # noqa: B027 — optional hook
        """Release connections."""
