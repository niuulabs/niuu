"""Factory for creating issue tracker adapters from integration connections."""

from __future__ import annotations

import logging

from niuu.ports.credentials import CredentialStorePort
from niuu.utils import import_class as _import_class
from tracker.models import IntegrationConnection
from tracker.ports import IssueTrackerProvider

logger = logging.getLogger(__name__)


class TrackerFactory:
    """Creates IssueTrackerProvider instances from IntegrationConnection configs.

    Resolves the credential from the CredentialStorePort, imports the adapter
    class, and instantiates it with the merged credential + config kwargs.
    """

    def __init__(self, credential_store: CredentialStorePort) -> None:
        self._credential_store = credential_store

    async def create(self, connection: IntegrationConnection) -> IssueTrackerProvider:
        """Create an IssueTrackerProvider from a connection definition.

        Args:
            connection: The integration connection with adapter class path,
                        credential name, and adapter-specific config.

        Returns:
            An instantiated IssueTrackerProvider.

        Raises:
            ValueError: If the credential is not found.
        """
        cred = await self._credential_store.get_value(
            "user",
            connection.owner_id,
            connection.credential_name,
        )
        if cred is None:
            raise ValueError(
                f"Credential '{connection.credential_name}' not found "
                f"for user '{connection.owner_id}'"
            )

        cls = _import_class(connection.adapter)
        kwargs = {**cred, **connection.config}
        instance = cls(**kwargs)
        logger.info(
            "Created tracker adapter: %s (user=%s)",
            connection.adapter.rsplit(".", 1)[-1],
            connection.owner_id,
        )
        return instance
