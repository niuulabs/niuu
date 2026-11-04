"""Identity of residents the Forge discovers rather than deploys.

A discovered resident (a Valkyrie deployed straight from infrastructure) is
known everywhere else by its own key, such as ``valkyrie-glitnir-k8s``. The
Forge keeps a runtime record for it so its sessions run through the same
controllers as a deployed resident's, and that record's id must be a UUID. The
id is derived from the key, so the Forge that keeps the record and the Ravn API
that lists the resident by its key name the same record without asking each
other.
"""

from __future__ import annotations

from uuid import UUID, uuid5

#: Fixed namespace for discovered-resident ids; changing it orphans every record.
DISCOVERED_RESIDENT_NAMESPACE = UUID("6f1d6c3e-0b7a-5c2e-9d84-2f5a1c7e9b30")


def discovered_resident_id(key: str) -> UUID:
    """The runtime id of the discovered resident with this key."""
    if not key.strip():
        raise ValueError("a discovered resident needs a non-empty key")
    return uuid5(DISCOVERED_RESIDENT_NAMESPACE, key)
