"""Niuu shared port interfaces."""

from niuu.ports.cli import CLITransport, EventCallback, TransportCapabilities
from niuu.ports.credentials import CredentialStorePort
from niuu.ports.embedded_database import EmbeddedDatabasePort
from niuu.ports.git import GitProvider
from niuu.ports.graphql import GraphQLClientPort
from niuu.ports.integrations import IntegrationRepository

__all__ = [
    "CLITransport",
    "CredentialStorePort",
    "EmbeddedDatabasePort",
    "EventCallback",
    "GitProvider",
    "GraphQLClientPort",
    "IntegrationRepository",
    "TransportCapabilities",
]
