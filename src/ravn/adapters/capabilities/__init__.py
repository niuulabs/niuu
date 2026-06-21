"""Capability discovery adapters."""

from ravn.adapters.capabilities.resident_discovery import CatalogWebCapabilityDiscoveryBackend
from ravn.adapters.capabilities.ting_workflows import TingWorkflowCapabilityAdapter

__all__ = [
    "CatalogWebCapabilityDiscoveryBackend",
    "TingWorkflowCapabilityAdapter",
]
