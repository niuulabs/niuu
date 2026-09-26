"""Operator-trusted MCP hostnames that may resolve to private addresses.

MCP discovery normally admits only hosts that resolve to public addresses. An
operator running MCP servers on their own cluster names those hosts here, either
exactly (``mcp.example.internal``) or as a ``*.domain`` suffix that matches any
subdomain but not the domain itself (``*.asgard.niuu.world``).
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable

_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_WILDCARD = "*."


def normalize_internal_host_pattern(pattern: str) -> str:
    """Return the canonical pattern, or raise for anything that is not a hostname.

    A wildcard must cover at least two labels so ``*`` or ``*.com`` cannot
    quietly switch the public-address check off for the whole internet.
    """
    value = pattern.strip().lower().rstrip(".")
    wildcard = value.startswith(_WILDCARD)
    name = value.removeprefix(_WILDCARD) if wildcard else value
    labels = name.split(".")
    if not name or not all(_LABEL.match(label) for label in labels):
        raise ValueError(
            f"Invalid internal MCP host {pattern!r}: use a hostname such as "
            "'mcp.example.internal' or a suffix such as '*.asgard.niuu.world'"
        )
    if wildcard and len(labels) < 2:
        raise ValueError(
            f"Internal MCP host {pattern!r} is too broad: a wildcard must name a "
            "domain with at least two labels, e.g. '*.asgard.niuu.world'"
        )
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return value
    raise ValueError(
        f"Internal MCP host {pattern!r} is an IP address: list the server's hostname, "
        "since TLS verifies the hostname"
    )


def is_internal_mcp_host(host: str, patterns: Iterable[str]) -> bool:
    """Whether ``host`` matches one of the normalized ``patterns``."""
    name = host.lower().rstrip(".")
    return any(
        name.endswith(pattern[1:]) if pattern.startswith(_WILDCARD) else name == pattern
        for pattern in patterns
    )
