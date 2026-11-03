"""How Forge reaches a live session's Skuld broker over HTTP.

Every server-side call Forge makes to a session's broker resolves it the same way:
the session's chat endpoint names the broker, path segments are quoted onto that
base, and OpenShell service hosts are routed through the in-cluster gateway. The
REST proxy routes (including the presented-file download) and the notification
image copies share these helpers so they cannot drift apart.
"""

from __future__ import annotations

from urllib.parse import quote, urlsplit, urlunsplit

DEFAULT_OPENSHELL_INTERNAL_GATEWAY_URL = "http://openshell.openshell.svc.cluster.local:8080"
OPENSHELL_SERVICE_HOST_SUFFIX = ".openshell.localhost"
#: The broker's HTTP API root and its presented-file route (``present-file`` staging).
BROKER_API_SEGMENT = "api"
PRESENTED_FILE_ROUTE = ("files", "presented")
_CHAT_PATH_SUFFIX = "/session"


def broker_base_url(chat_endpoint: str) -> str:
    """The broker's HTTP base URL for a session's WebSocket chat endpoint."""
    base_url = chat_endpoint.replace("wss://", "https://").replace("ws://", "http://")
    if base_url.endswith(_CHAT_PATH_SUFFIX):
        base_url = base_url[: -len(_CHAT_PATH_SUFFIX)]
    return base_url


def session_proxy_url(base_url: str, *path_segments: str) -> str:
    """Build a validated session-pod URL from a trusted base and encoded path segments."""
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Session proxy target must be an absolute http(s) URL")

    segments = [quote(segment, safe="") for segment in path_segments]
    base_path = parsed.path.rstrip("/")
    suffix = "/".join(segments)
    if base_path and suffix:
        path = f"{base_path}/{suffix}"
    elif base_path:
        path = base_path
    elif suffix:
        path = f"/{suffix}"
    else:
        path = "/"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def presented_file_url(base_url: str, file_id: str) -> str:
    """The broker URL serving a presented file by its opaque id."""
    return session_proxy_url(base_url, BROKER_API_SEGMENT, *PRESENTED_FILE_ROUTE, file_id)


def server_side_http_proxy_target(
    url: str,
    *,
    gateway_url: str = DEFAULT_OPENSHELL_INTERNAL_GATEWAY_URL,
) -> tuple[str, dict[str, str]]:
    """Route an OpenShell service URL through the in-cluster gateway."""
    parsed = urlsplit(url)
    if not parsed.hostname or not parsed.hostname.endswith(OPENSHELL_SERVICE_HOST_SUFFIX):
        return url, {}

    gateway = urlsplit(gateway_url)
    if not gateway.scheme or not gateway.netloc:
        raise ValueError("OpenShell internal gateway must be an absolute URL")

    routed_url = urlunsplit(
        (gateway.scheme, gateway.netloc, parsed.path, parsed.query, parsed.fragment)
    )
    return routed_url, {"Host": parsed.netloc}
