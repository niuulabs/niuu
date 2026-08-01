"""HTTP client adapter for cluster-local Observatory topology fragments."""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlsplit, urlunsplit

import httpx

from niuu.domain.models import RegisteredInstance
from niuu.domain.observatory import ObservatoryFragment
from niuu.ports.observatory_topology import ObservatoryTopologyClientPort


def _fragment_url(base_url: str) -> str:
    """Resolve an instance's fragment endpoint.

    Accepts a bare host, a `/api/v1/observatory` prefix, or the full path, so a
    registered instance can be recorded either way without the caller having to
    know which convention was used.
    """
    parsed = urlsplit(base_url.rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Observatory base URL must use http or https")
    if parsed.username or parsed.password:
        raise ValueError("Observatory base URL must not embed credentials")
    path = parsed.path.rstrip("/")
    if path.endswith("/api/v1/observatory/fragment"):
        pass
    elif path.endswith("/api/v1/observatory"):
        path = f"{path}/fragment"
    else:
        path = f"{path}/api/v1/observatory/fragment"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


class HttpObservatoryTopologyClient(ObservatoryTopologyClientPort):
    """Call one Observatory's fragment endpoint with a bounded timeout."""

    def __init__(
        self,
        *,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def fetch_fragment(
        self,
        instance: RegisteredInstance,
        *,
        headers: Mapping[str, str],
    ) -> ObservatoryFragment:
        async with httpx.AsyncClient(
            timeout=self._timeout_seconds,
            follow_redirects=False,
            transport=self._transport,
        ) as client:
            response = await client.get(
                _fragment_url(instance.base_url),
                headers=dict(headers),
            )
            response.raise_for_status()
            return ObservatoryFragment.model_validate(response.json())
