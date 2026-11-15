"""Minting a session's realtime voice credential from its owner's own provider key.

The owner's API key for each realtime provider lives in Forge's credential store, never
in the session pod. A session's broker asks Forge (with its ``forge_session``
credential) for a short-lived provider credential; this service reads the owner's key
for the provider the call asked for and has that provider's minter mint with it.

A provider that is not configured, or whose key the owner has not stored, is an error
that says how to fix it. No other provider is ever tried in its place.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass

from volundr.domain.ports import CredentialStorePort
from volundr.ports.realtime_voice import RealtimeCredential, RealtimeCredentialMinterPort

logger = logging.getLogger(__name__)

#: The credential owner type the owner's provider keys are stored under.
USER_OWNER_TYPE = "user"


class UnknownVoiceProviderError(LookupError):
    """The call asked for a realtime provider this Forge is not configured for."""


class VoiceProviderKeyMissingError(LookupError):
    """The owner has not stored the API key for the provider the call asked for."""


@dataclass(frozen=True)
class VoiceProviderBinding:
    """One configured provider: its minter and where the owner's key is stored."""

    minter: RealtimeCredentialMinterPort
    #: The owner's stored credential (owner type ``user``) holding the key.
    credential_name: str
    #: The field of that credential holding the key.
    credential_key: str


class SessionVoiceCredentialService:
    """Mint a realtime credential for a session's owner through the owner's own key."""

    def __init__(
        self,
        credential_store: CredentialStorePort,
        providers: Mapping[str, VoiceProviderBinding],
    ) -> None:
        self._store = credential_store
        self._providers = dict(providers)

    @property
    def provider_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))

    async def mint(
        self,
        *,
        owner_id: str,
        provider: str,
        model: str,
        voice: str,
        instructions: str,
        reasoning: str,
    ) -> RealtimeCredential:
        binding = self._providers.get(provider)
        if binding is None:
            raise UnknownVoiceProviderError(
                f"This Forge mints no realtime voice credentials for provider {provider!r}; "
                f"configured providers: {', '.join(self.provider_names) or 'none'} "
                "(voice.providers)"
            )
        api_key = await self._api_key(owner_id, provider, binding)
        credential = await binding.minter.mint(
            api_key=api_key,
            model=model,
            voice=voice,
            instructions=instructions,
            reasoning=reasoning,
        )
        logger.info(
            "Minted a %s realtime voice credential for owner %s (expires_at_ms=%s)",
            provider,
            owner_id,
            credential.expires_at_ms,
        )
        return credential

    async def _api_key(self, owner_id: str, provider: str, binding: VoiceProviderBinding) -> str:
        remedy = (
            f"store a user credential named {binding.credential_name!r} with field "
            f"{binding.credential_key!r} holding your {provider} API key"
        )
        data = await self._store.get_value(USER_OWNER_TYPE, owner_id, binding.credential_name)
        if data is None:
            raise VoiceProviderKeyMissingError(
                f"No {provider} API key is stored for this session's owner: {remedy}"
            )
        key = data.get(binding.credential_key)
        if not key or not key.strip():
            raise VoiceProviderKeyMissingError(
                f"The stored credential {binding.credential_name!r} has no "
                f"{binding.credential_key!r} field (or it is empty): {remedy}"
            )
        return key.strip()
