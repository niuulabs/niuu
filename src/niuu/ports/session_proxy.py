"""Shared target description for proxying a session service."""

from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit


@dataclass(frozen=True)
class SessionProxyTarget:
    """Session service URL plus the network address used to reach it.

    OpenShell service URLs carry the sandbox route in their HTTP Host header,
    while the actual connection must go to the in-cluster gateway address.
    """

    service_url: str
    connect_host: str
    connect_port: int
    connect_secure: bool = False

    def http_url(self, path: str) -> tuple[str, str]:
        """The URL that connects to ``path`` and the Host header naming the service."""
        host = self.connect_host
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        scheme = "https" if self.connect_secure else "http"
        url = urlunsplit((scheme, f"{host}:{self.connect_port}", path, "", ""))
        return url, urlsplit(self.service_url).netloc
