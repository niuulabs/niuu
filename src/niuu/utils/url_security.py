"""Shared SSRF protection utilities for URL validation.

Used by both web_fetch and browser tool adapters to block requests to
private/reserved IP ranges.
"""

from __future__ import annotations

import ipaddress
import socket

_PRIVATE_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]


def is_private_ip(ip: str) -> bool:
    """Return True if the IP address falls within a private/reserved range."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True  # Unparseable — block by default.
    return any(addr in net for net in _PRIVATE_NETWORKS)


def check_ssrf(hostname: str) -> str | None:
    """Resolve *hostname* and return an error string if it maps to a private address.

    Returns None if the hostname is safe to connect to.
    Blocks DNS rebinding: resolves the hostname and checks every returned address.
    """
    try:
        results = socket.getaddrinfo(hostname, None)
    except OSError:
        return f"Blocked: could not resolve hostname '{hostname}'"

    for _family, _type, _proto, _canonname, sockaddr in results:
        ip = sockaddr[0]
        if is_private_ip(ip):
            return f"Blocked: '{hostname}' resolves to a private/reserved address"

    return None
