"""Port for minting short-lived realtime voice credentials.

A realtime voice model runs on the owner's device and talks to the provider directly.
Forge never relays the audio: it hands the device a credential that expires within
minutes, minted from the owner's own provider key, which neither the device nor the
session ever sees.
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

    #: The provider's name, as it appears in messages.
    provider: str

    @abstractmethod
    async def mint(
        self,
        *,
        api_key: str,
        model: str,
        voice: str,
        instructions: str,
        reasoning: str = "",
    ) -> RealtimeCredential:
        """Return a credential for *model* minted with *api_key*.

        Raises :class:`RealtimeCredentialError` when the provider refuses, fails,
        or answers without a usable credential and expiry.
        """
