"""The memory gateway's connector route, called over HTTP with Ting's service credential."""

from __future__ import annotations

import logging

import httpx

from niuu.ports.http_auth import HttpAuthPort
from ting.domain.memory_imports import SourceAccepted, SourceGatewayError, SourceWrite
from ting.ports.memory_imports import MemorySourceGateway

logger = logging.getLogger(__name__)

#: The route, under the Mimir API prefix the gateway URL ends with.
SOURCES_PATH = "/memory/sources"
_AUTH_REJECTED = frozenset({401, 403})


class HttpMemorySourceGateway(MemorySourceGateway):
    """``POST {base_url}/memory/sources``: one immutable source at ``tool`` trust.

    ``base_url`` is the Mimir API prefix (for example ``http://mimir:8080/api/v1/mimir``).
    ``auth`` supplies the platform service credential the gateway admits connectors by.
    """

    def __init__(self, *, base_url: str, auth: HttpAuthPort, timeout_seconds: float) -> None:
        if not base_url.strip():
            raise ValueError(
                "the memory gateway URL is empty: set memory_imports.gateway_url to the Mimir "
                "API prefix, for example http://mimir:8080/api/v1/mimir"
            )
        if timeout_seconds <= 0:
            raise ValueError("memory_imports.gateway_timeout_seconds must be positive")
        self._url = base_url.rstrip("/") + SOURCES_PATH
        self._auth = auth
        self._timeout_seconds = timeout_seconds

    async def write_source(self, source: SourceWrite) -> SourceAccepted:
        body = {
            "tenant_id": source.tenant_id,
            "layer": source.layer,
            "connector": source.connector,
            "external_id": source.external_id,
            "title": source.title,
            "content": source.content,
            "kind": source.kind,
            "origin_url": source.origin_url,
            "fetched_at": source.fetched_at.isoformat(),
            "upstream_updated_at": source.upstream_updated_at.isoformat(),
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(self._url, json=body, headers=self._auth.headers())
        except httpx.HTTPError as exc:
            raise SourceGatewayError(
                f"the memory gateway at {self._url} could not be reached for "
                f"{source.external_id}: {exc!r}; check memory_imports.gateway_url"
            ) from exc
        if response.status_code in _AUTH_REJECTED:
            # A refreshable credential is discarded so the next interval presents a fresh one.
            self._auth.invalidate()
            raise SourceGatewayError(
                f"the memory gateway refused Ting's credential ({response.status_code}) for "
                f"{source.external_id}: {response.text}; the credential must be a platform "
                "service whose subject is in memory.connector_service_subjects "
                "(memory_imports.gateway_auth)"
            )
        if response.status_code != httpx.codes.OK:
            raise SourceGatewayError(
                f"the memory gateway answered {response.status_code} for {source.external_id}: "
                f"{response.text}"
            )
        malformed = SourceGatewayError(
            f"the memory gateway's answer for {source.external_id} is not "
            f'{{"source_id", "layer", "new"}}: {response.text}'
        )
        try:
            answer = response.json()
            source_id, layer, new = answer["source_id"], answer["layer"], answer["new"]
        except (ValueError, KeyError, TypeError) as exc:
            raise malformed from exc
        if not (isinstance(source_id, str) and isinstance(layer, str) and isinstance(new, bool)):
            raise malformed
        return SourceAccepted(source_id=source_id, layer=layer, new=new)
