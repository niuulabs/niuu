"""Port for minting short-lived realtime voice credentials.

A realtime voice model runs on the caller's device and talks to the provider
directly. The resident never relays the audio; it hands the device a credential
that expires within minutes, minted from a provider key the device never sees.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class RealtimeCredentialError(RuntimeError):
    """The provider refused or failed to mint a credential."""


@dataclass(frozen=True)
class RealtimeCredential:
    """A minted credential and when it stops working."""

    token: str
    #: Epoch milliseconds.
    expires_at_ms: int
    #: The model the credential is for (as minted, or as requested when the
    #: provider does not scope credentials to a model).
    model: str
    #: Whether the session instructions and voice were baked into the credential.
    session_bound: bool


class RealtimeCredentialMinterPort(ABC):
    """Mint a short-lived credential for one realtime provider."""

    @abstractmethod
    async def mint(
        self,
        *,
        model: str,
        voice: str,
        instructions: str,
        reasoning: str = "",
    ) -> RealtimeCredential:
        """Return a credential for *model*; raise :class:`RealtimeCredentialError`."""
