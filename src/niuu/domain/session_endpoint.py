"""Browser-facing session endpoint normalization."""

from __future__ import annotations

from urllib.parse import quote, urlsplit, urlunsplit

OPENSHELL_SERVICE_HOST_SUFFIX = ".openshell.localhost"


def public_session_endpoint(
    endpoint: str | None,
    *,
    session_id: str = "",
    public_host: str | None = None,
) -> str | None:
    """Normalize an internal session endpoint for a browser-facing response."""
    if not endpoint:
        return endpoint

    try:
        parsed = urlsplit(endpoint)
    except ValueError:
        return endpoint

    if session_id and parsed.hostname and parsed.hostname.endswith(OPENSHELL_SERVICE_HOST_SUFFIX):
        return f"/s/{quote(session_id, safe='')}/session"

    if parsed.hostname != "127.0.0.1":
        return endpoint

    normalized_host = str(public_host or "127.0.0.1").strip() or "127.0.0.1"
    browser_host = "localhost" if normalized_host == "127.0.0.1" else normalized_host
    netloc = browser_host
    if parsed.port is not None:
        netloc = f"{browser_host}:{parsed.port}"

    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
