"""The integration connections a session's current launch attached, recorded on it.

A session that launches other sessions does so with its own ``forge_session``
credential. That credential is not the owner's: it cannot be forwarded to a shared
Connections API in another cluster, and it should not reach connections the session
itself was never given. So a launch made with it does not look connections up again: it
takes them from the record of the calling session's launch, which was resolved with the
owner's own credential.

Each launch replaces the whole record (``RESOLVED_CONNECTIONS_KEY``) with what that launch
resolved, so the record never outlives its launch. A caller cannot set it: the session
service drops whatever a request carried. The record holds connection metadata only (the
same fields the Connections API returns); credentials stay in the credential store.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from niuu.domain.models import IntegrationConnection, IntegrationType

#: Where the record lives in ``Session.workload_config``.
RESOLVED_CONNECTIONS_KEY = "_resolved_connections"


class ResolvedConnectionsRecordError(ValueError):
    """The record on a session is not one this Forge wrote."""


def with_resolved_connections(
    workload_config: Mapping[str, Any], connections: Iterable[IntegrationConnection]
) -> dict[str, Any]:
    """``workload_config`` carrying ``connections`` as its launch's record."""
    return {
        **workload_config,
        RESOLVED_CONNECTIONS_KEY: [_to_record(connection) for connection in connections],
    }


def resolved_connections_of(
    workload_config: Mapping[str, Any] | None,
) -> list[IntegrationConnection] | None:
    """The connections the session's current launch attached.

    ``None`` when the session has no record (it has not been launched since Forge began
    recording them); an empty list when its launch attached none. Raises
    ``ResolvedConnectionsRecordError`` for a record this Forge could not have written.
    """
    if not workload_config or RESOLVED_CONNECTIONS_KEY not in workload_config:
        return None
    records = workload_config[RESOLVED_CONNECTIONS_KEY]
    if not isinstance(records, list):
        raise ResolvedConnectionsRecordError(
            f"{RESOLVED_CONNECTIONS_KEY} must be a list of connections, got "
            f"{type(records).__name__}; restart the session to record its connections again"
        )
    return [_from_record(record) for record in records]


def without_resolved_connections(workload_config: Mapping[str, Any]) -> dict[str, Any]:
    """The workload config without the record (what contributors and callers see)."""
    return {key: value for key, value in workload_config.items() if key != RESOLVED_CONNECTIONS_KEY}


def _to_record(connection: IntegrationConnection) -> dict[str, Any]:
    return {
        "id": connection.id,
        "owner_id": connection.owner_id,
        "integration_type": str(connection.integration_type),
        "adapter": connection.adapter,
        "credential_name": connection.credential_name,
        "config": dict(connection.config),
        "enabled": connection.enabled,
        "created_at": connection.created_at.isoformat(),
        "updated_at": connection.updated_at.isoformat(),
        "slug": connection.slug,
        "project_id": connection.project_id,
        "connected_by": connection.connected_by,
    }


def _from_record(record: Any) -> IntegrationConnection:
    if not isinstance(record, Mapping):
        raise ResolvedConnectionsRecordError(
            f"{RESOLVED_CONNECTIONS_KEY} holds a {type(record).__name__}, not a connection; "
            "restart the session to record its connections again"
        )
    try:
        return IntegrationConnection(
            id=str(record["id"]),
            owner_id=str(record["owner_id"]),
            integration_type=IntegrationType(record["integration_type"]),
            adapter=str(record["adapter"]),
            credential_name=str(record["credential_name"]),
            config=dict(record["config"]),
            enabled=bool(record["enabled"]),
            created_at=datetime.fromisoformat(record["created_at"]),
            updated_at=datetime.fromisoformat(record["updated_at"]),
            slug=str(record["slug"]),
            project_id=record["project_id"],
            connected_by=str(record["connected_by"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ResolvedConnectionsRecordError(
            f"{RESOLVED_CONNECTIONS_KEY} holds a connection Forge cannot read ({exc}); "
            "restart the session to record its connections again"
        ) from exc
