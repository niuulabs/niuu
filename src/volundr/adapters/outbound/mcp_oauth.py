"""MCP authentication discovery using the official SDK's metadata contracts.

Discovery addresses are untrusted. Resolve and pin a public destination for
every request, retain TLS hostname verification, and never follow redirects.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit

import httpx
from mcp import ClientSession
from mcp.client.auth.utils import (
    build_oauth_authorization_server_metadata_discovery_urls,
    build_protected_resource_metadata_discovery_urls,
    extract_resource_metadata_from_www_auth,
    extract_scope_from_www_auth,
    get_client_metadata_scopes,
    validate_metadata_issuer,
)
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthMetadata, ProtectedResourceMetadata


class MCPDiscoveryError(ValueError):
    """Safe, actionable discovery error; never includes a provider response."""


def validate_mcp_url(url: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or any(
            key.lower() in {"access_token", "refresh_token", "api_key", "token", "key"}
            for key, _ in parse_qsl(parsed.query)
        )
    ):
        raise MCPDiscoveryError(
            "MCP authentication requires HTTPS URLs without embedded credentials"
        )
    try:
        parsed.port
    except ValueError as exc:
        raise MCPDiscoveryError("Invalid MCP endpoint port") from exc


class PublicEndpointTransport(httpx.AsyncBaseTransport):
    """Pin DNS results to prevent discovery from accessing internal services."""

    def __init__(self) -> None:
        # Pool origins would be keyed by the pinned IP, not the TLS hostname.
        # Do not reuse a connection across two issuers that share an address.
        self._transport = httpx.AsyncHTTPTransport(limits=httpx.Limits(max_keepalive_connections=0))

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        validate_mcp_url(str(request.url))
        addresses = await asyncio.get_running_loop().getaddrinfo(
            request.url.host, request.url.port or 443, type=socket.SOCK_STREAM
        )
        ips = [address[4][0] for address in addresses]
        prohibited_translation = (
            ipaddress.ip_network("64:ff9b::/96"),
            ipaddress.ip_network("64:ff9b:1::/48"),
            ipaddress.ip_network("2002::/16"),
        )
        if not ips or any(
            not ipaddress.ip_address(ip).is_global
            or any(ipaddress.ip_address(ip) in network for network in prohibited_translation)
            for ip in ips
        ):
            raise MCPDiscoveryError("MCP authentication endpoints must resolve to public addresses")
        original = request.url
        request.headers["Host"] = original.netloc.decode("ascii")
        request.extensions["sni_hostname"] = original.host
        request.url = original.copy_with(host=ips[0])
        try:
            return await self._transport.handle_async_request(request)
        finally:
            request.url = original

    async def aclose(self) -> None:
        await self._transport.aclose()


@dataclass(frozen=True)
class MCPDiscovery:
    server_url: str
    resource: str
    metadata: OAuthMetadata
    scope: str


class MCPOAuthDiscovery:
    def __init__(self, *, request_timeout: float = 15.0) -> None:
        self._timeout = request_timeout

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=PublicEndpointTransport(),
            timeout=self._timeout,
            follow_redirects=False,
            trust_env=False,
        )

    async def initialize(self, server_url: str, headers: dict[str, str]) -> None:
        """Verify a connection without listing tools or accessing user data."""
        async with asyncio.timeout(self._timeout), self.client() as client:
            client.headers.update(headers)
            async with streamable_http_client(server_url, http_client=client) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()

    async def discover(self, server_url: str) -> MCPDiscovery:
        validate_mcp_url(server_url)
        async with self.client() as client:
            # A GET may be an SSE stream: only the status and challenge headers
            # are needed, so do not consume its response body.
            async with client.stream("GET", server_url) as challenge:
                metadata_url = extract_resource_metadata_from_www_auth(challenge)
                scope = extract_scope_from_www_auth(challenge)
            resource = None
            for url in build_protected_resource_metadata_discovery_urls(metadata_url, server_url):
                response = await client.get(url)
                if response.status_code in {404, 405}:
                    continue
                if response.status_code != 200:
                    raise MCPDiscoveryError("MCP resource metadata could not be retrieved")
                resource = ProtectedResourceMetadata.model_validate_json(response.content)
                break
            if resource is None or not resource.authorization_servers:
                raise MCPDiscoveryError(
                    "Server does not advertise OAuth; use its API token or registered integration"
                )
            # A credential must never be issued for a different resource than
            # the exact endpoint the user selected.
            if str(resource.resource).rstrip("/") != server_url.rstrip("/"):
                raise MCPDiscoveryError("MCP resource metadata does not match the selected server")
            issuer = str(resource.authorization_servers[0])
            validate_mcp_url(issuer)
            for url in build_oauth_authorization_server_metadata_discovery_urls(issuer, server_url):
                response = await client.get(url)
                if response.status_code in {404, 405}:
                    continue
                if response.status_code != 200:
                    raise MCPDiscoveryError("OAuth server metadata could not be retrieved")
                metadata = OAuthMetadata.model_validate_json(response.content)
                validate_metadata_issuer(metadata, issuer)
                if "S256" not in (metadata.code_challenge_methods_supported or []):
                    raise MCPDiscoveryError("OAuth server must advertise PKCE S256 support")
                for endpoint in (
                    metadata.authorization_endpoint,
                    metadata.token_endpoint,
                    metadata.registration_endpoint,
                ):
                    if endpoint:
                        validate_mcp_url(str(endpoint))
                return MCPDiscovery(
                    server_url=server_url,
                    resource=str(resource.resource),
                    metadata=metadata,
                    scope=get_client_metadata_scopes(scope, resource) or "",
                )
        raise MCPDiscoveryError("OAuth server does not publish valid discovery metadata")
