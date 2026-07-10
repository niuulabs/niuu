"""Shared target description for proxying a session service."""

from dataclasses import dataclass


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
