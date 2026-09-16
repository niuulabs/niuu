"""Discovery never trusts an advertised issuer, resource, or network destination."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from volundr.adapters.outbound.mcp_oauth import (
    MCPDiscoveryError,
    MCPOAuthDiscovery,
    PublicEndpointTransport,
)


@pytest.mark.asyncio
async def test_discovery_uses_challenge_and_oidc_path_with_authoritative_scope():
    requests = []

    def respond(request):
        requests.append(str(request.url))
        match request.url.path:
            case "/mcp":
                return httpx.Response(
                    401,
                    headers={
                        "WWW-Authenticate": (
                            'Bearer resource_metadata="https://tools.example/metadata", '
                            'scope="read"'
                        )
                    },
                )
            case "/metadata":
                return httpx.Response(
                    200,
                    json={
                        "resource": "https://tools.example/mcp",
                        "authorization_servers": ["https://auth.example/tenant"],
                        "scopes_supported": ["read", "write"],
                    },
                )
            case "/tenant/.well-known/openid-configuration":
                return httpx.Response(
                    200,
                    json={
                        "issuer": "https://auth.example/tenant",
                        "authorization_endpoint": "https://auth.example/authorize",
                        "token_endpoint": "https://auth.example/token",
                        "response_types_supported": ["code"],
                        "code_challenge_methods_supported": ["S256"],
                    },
                )
        return httpx.Response(404)

    discovery = MCPOAuthDiscovery()
    with patch.object(
        discovery, "client", return_value=httpx.AsyncClient(transport=httpx.MockTransport(respond))
    ):
        result = await discovery.discover("https://tools.example/mcp")
    assert result.scope == "read"
    assert requests[-3:] == [
        "https://auth.example/.well-known/oauth-authorization-server/tenant",
        "https://auth.example/.well-known/openid-configuration/tenant",
        "https://auth.example/tenant/.well-known/openid-configuration",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("ip", ["127.0.0.1", "169.254.169.254", "10.0.0.1", "::1"])
async def test_discovery_blocks_internal_destinations(ip):
    transport = PublicEndpointTransport()
    with patch("asyncio.get_running_loop") as loop:
        loop.return_value.getaddrinfo = AsyncMock(return_value=[(0, 0, 0, "", (ip, 443))])
        with pytest.raises(MCPDiscoveryError, match="public addresses"):
            await transport.handle_async_request(httpx.Request("GET", "https://evil.example/meta"))
    await transport.aclose()


@pytest.mark.asyncio
async def test_discovery_pins_public_ip_but_preserves_tls_hostname():
    transport = PublicEndpointTransport()
    observed = []

    async def handle(request):
        observed.append(
            (request.url.host, request.headers["Host"], request.extensions["sni_hostname"])
        )
        return httpx.Response(200)

    request = httpx.Request("GET", "https://tools.example/meta")
    with (
        patch("asyncio.get_running_loop") as loop,
        patch.object(transport._transport, "handle_async_request", side_effect=handle),
    ):
        loop.return_value.getaddrinfo = AsyncMock(return_value=[(0, 0, 0, "", ("1.1.1.1", 443))])
        await transport.handle_async_request(request)
    assert observed == [("1.1.1.1", "tools.example", "tools.example")]
    assert request.url.host == "tools.example"
    await transport.aclose()


@pytest.mark.parametrize(
    "url",
    [
        "http://tools.example/mcp",
        "https://user:secret@tools.example/mcp",
        "https://tools.example/mcp?access_token=secret",
        "https://tools.example:bad/mcp",
    ],
)
def test_urls_reject_embedded_credentials_and_insecure_transport(url):
    from volundr.adapters.outbound.mcp_oauth import validate_mcp_url

    with pytest.raises(ValueError):
        validate_mcp_url(url)


async def test_shared_ip_does_not_reuse_another_hosts_tls_connection():
    import httpcore

    tls_hosts = []
    response = b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"

    class CertificateCheckedStream(httpcore.AsyncMockStream):
        async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            tls_hosts.append(server_hostname)
            # Model an endpoint whose certificate covers only the first host.
            if server_hostname != "first.example":
                raise httpcore.ConnectError("certificate hostname mismatch")
            return self

    class SharedIPBackend(httpcore.AsyncMockBackend):
        async def connect_tcp(self, host, port, **kwargs):
            assert host == "1.1.1.1"
            return CertificateCheckedStream([response, response])

    transport = PublicEndpointTransport()
    transport._transport._pool._network_backend = SharedIPBackend([])
    async with httpx.AsyncClient(transport=transport) as client:
        with patch("asyncio.get_running_loop") as loop:
            loop.return_value.getaddrinfo = AsyncMock(
                return_value=[(0, 0, 0, "", ("1.1.1.1", 443))]
            )
            assert (await client.get("https://first.example/metadata")).status_code == 200
            with pytest.raises(httpx.ConnectError, match="certificate hostname mismatch"):
                await client.get("https://second.example/metadata")
    assert tls_hosts == ["first.example", "second.example"]
