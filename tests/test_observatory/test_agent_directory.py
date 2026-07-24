"""Tests for the principal-aware local Observatory Agent Directory."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from a2a.types import AgentCard
from a2a.utils.signing import create_agent_card_signer
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from google.protobuf.json_format import MessageToDict
from jwt.algorithms import RSAAlgorithm

from niuu.domain.agent_directory import AgentDirectoryFilters
from niuu.domain.models import Principal
from niuu.ports.agent_cards import AgentCardResolutionError, ResolvedAgentCard
from observatory.a2a_cards import HttpAgentCardResolver
from observatory.agent_directory import AgentDirectoryService
from observatory.discovery import ObservatoryDiscoveryService
from observatory.entity_discovery import DiscoveredEntity, DiscoveryResult


def _card_payload(
    name: str = "Workflow Agent",
    *,
    skill: str = "code",
    tag: str = "engineering",
) -> dict[str, Any]:
    return {
        "name": name,
        "description": "Completes an addressable workflow",
        "supportedInterfaces": [
            {
                "url": "https://agents.example.test/a2a",
                "protocolBinding": "JSONRPC",
                "protocolVersion": "1.0",
            }
        ],
        "version": "1.2.3",
        "capabilities": {"streaming": True},
        "securitySchemes": {
            "bearer": {
                "httpAuthSecurityScheme": {
                    "scheme": "bearer",
                    "bearerFormat": "JWT",
                }
            }
        },
        "securityRequirements": [{"schemes": {"bearer": {"list": []}}}],
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["application/json"],
        "skills": [
            {
                "id": skill,
                "name": skill.title(),
                "description": f"Performs {skill}",
                "tags": [tag],
            }
        ],
    }


class _StaticDiscoveryAdapter:
    def __init__(self, result: DiscoveryResult) -> None:
        self._result = result

    async def discover(self) -> DiscoveryResult:
        return self._result


class _StubAuth:
    async def headers(self) -> dict[str, str]:
        return {}


class _StubCardResolver:
    def __init__(self, cards: dict[str, ResolvedAgentCard | Exception]) -> None:
        self.cards = cards
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    async def resolve(
        self,
        card_url: str,
        *,
        principal_key: str,
        headers: dict[str, str],
    ) -> ResolvedAgentCard:
        self.calls.append((card_url, principal_key, dict(headers)))
        result = self.cards[card_url]
        if isinstance(result, Exception):
            raise result
        return result


def _resolved_card(
    name: str,
    *,
    skill: str,
    tag: str,
    card_hash: str,
) -> ResolvedAgentCard:
    from niuu.domain.agent_directory import AgentInterface

    return ResolvedAgentCard(
        name=name,
        description=f"{name} description",
        version="1.0.0",
        skills=(skill,),
        tags=(tag,),
        default_input_modes=("text/plain",),
        default_output_modes=("application/json",),
        supported_interfaces=(
            AgentInterface(
                url="https://agents.example.test/a2a",
                protocolBinding="JSONRPC",
                protocolVersion="1.0",
            ),
        ),
        capabilities={"streaming": True},
        card_hash=card_hash,
    )


def _entity(
    session_id: str,
    owner_id: str,
    card_url: str,
    *,
    tenant_id: str = "tenant-a",
    environment_members: list[str] | None = None,
    visibility: str = "user",
) -> DiscoveredEntity:
    return DiscoveredEntity(
        id=f"runtime:noatun:skuld:skuld:{session_id}",
        kind="skuld",
        name=session_id,
        cluster="noatun",
        namespace="skuld",
        status="healthy",
        source_kind="volundr-session",
        source_uid=session_id,
        endpoints={"a2aCard": card_url},
        metadata={
            "ownerId": owner_id,
            "tenantId": tenant_id,
            "visibility": visibility,
            "agentKind": "workflow-session",
            "environmentId": "environment-a",
            "environmentMemberIds": environment_members or [],
            "activity": "tooling",
            "lastActive": "2026-07-14T12:00:00Z",
        },
    )


def _service(
    entities: list[DiscoveredEntity],
    resolver: _StubCardResolver,
) -> AgentDirectoryService:
    discovery = ObservatoryDiscoveryService(
        guild_url="http://guild.test",
        auth=_StubAuth(),  # type: ignore[arg-type]
        discovery_adapter=_StaticDiscoveryAdapter(DiscoveryResult(entities=entities)),
    )
    return AgentDirectoryService(
        discovery=discovery,
        card_resolver=resolver,  # type: ignore[arg-type]
        instance_id="observatory-noatun",
        cluster_id="noatun",
        max_concurrency=2,
    )


def _principal(user_id: str = "user-a", tenant_id: str = "tenant-a") -> Principal:
    return Principal(user_id=user_id, email="", tenant_id=tenant_id, roles=["member"])


@pytest.mark.asyncio
async def test_directory_filters_before_card_fetch_and_preserves_topology_identity() -> None:
    visible_url = "https://visible.example.test/.well-known/agent-card.json"
    hidden_url = "https://hidden.example.test/.well-known/agent-card.json"
    resolver = _StubCardResolver(
        {
            visible_url: _resolved_card(
                "Builder",
                skill="code",
                tag="engineering",
                card_hash="visible-hash",
            ),
            hidden_url: _resolved_card(
                "Hidden",
                skill="finance",
                tag="restricted",
                card_hash="hidden-hash",
            ),
        }
    )
    service = _service(
        [
            _entity("session-a", "user-a", visible_url),
            _entity("session-b", "user-b", hidden_url),
        ],
        resolver,
    )

    page = await service.list_agents(
        _principal(),
        headers={"authorization": "Bearer principal-a"},
        filters=AgentDirectoryFilters(
            skills=("code",),
            tags=("engineering",),
            kinds=("workflow-session",),
            statuses=("healthy",),
            environment_ids=("environment-a",),
            cluster_ids=("noatun",),
            instance_ids=("observatory-noatun",),
        ),
    )

    assert len(page.items) == 1
    assert page.items[0].source_agent_id == "session-a"
    assert page.items[0].topology_node_id == "runtime:noatun:skuld:skuld:session-a"
    assert page.items[0].supported_interfaces[0].protocol_version == "1.0"
    assert resolver.calls == [
        (
            visible_url,
            "tenant-a\0user-a",
            {"authorization": "Bearer principal-a"},
        )
    ]


@pytest.mark.asyncio
async def test_directory_hides_cross_tenant_owner_and_environment_non_member() -> None:
    card_url = "https://agent.example.test/.well-known/agent-card.json"
    resolver = _StubCardResolver(
        {
            card_url: _resolved_card(
                "Builder",
                skill="code",
                tag="engineering",
                card_hash="hash",
            )
        }
    )
    service = _service(
        [
            _entity(
                "session-a",
                "user-a",
                card_url,
                environment_members=["user-b"],
            )
        ],
        resolver,
    )

    page = await service.list_agents(_principal(), headers={})
    cross_tenant = await service.list_agents(
        _principal(tenant_id="tenant-b"),
        headers={},
    )

    assert page.items == []
    assert cross_tenant.items == []
    assert resolver.calls == []
    assert await service.get_agent("session-a", _principal(), headers={}) is None


@pytest.mark.asyncio
async def test_directory_fails_closed_without_authoritative_environment_membership() -> None:
    card_url = "https://agent.example.test/.well-known/agent-card.json"
    resolver = _StubCardResolver(
        {
            card_url: _resolved_card(
                "Builder",
                skill="code",
                tag="engineering",
                card_hash="hash",
            )
        }
    )
    service = _service(
        [_entity("session-a", "user-b", card_url, visibility="tenant")],
        resolver,
    )

    page = await service.list_agents(_principal(), headers={})

    assert page.items == []
    assert resolver.calls == []


@pytest.mark.asyncio
async def test_directory_returns_visible_card_warning_without_failing_page() -> None:
    card_url = "https://agent.example.test/.well-known/agent-card.json"
    resolver = _StubCardResolver({card_url: AgentCardResolutionError("card timed out")})
    service = _service([_entity("session-a", "user-a", card_url)], resolver)

    page = await service.list_agents(_principal(), headers={})

    assert page.items == []
    assert page.partial is True
    assert page.sources[0].status == "degraded"
    assert page.warnings[0].source_agent_id == "session-a"
    assert page.warnings[0].code == "agent-card-unavailable"


@pytest.mark.asyncio
async def test_http_resolver_validates_and_conditionally_revalidates_agent_cards() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 2:
            assert request.headers["if-none-match"] == '"card-v1"'
            return httpx.Response(304, headers={"Cache-Control": "max-age=60"})
        return httpx.Response(
            200,
            json=_card_payload(),
            headers={"ETag": '"card-v1"', "Cache-Control": "max-age=0"},
        )

    resolver = HttpAgentCardResolver(
        timeout_seconds=1.0,
        default_cache_ttl_seconds=5.0,
        signature_algorithms=["RS256"],
        authenticated_card_origins=["https://agent.example.test"],
        transport=httpx.MockTransport(handler),
    )
    url = "https://agent.example.test/.well-known/agent-card.json"

    first = await resolver.resolve(
        url,
        principal_key="tenant-a\0user-a",
        headers={"authorization": "Bearer caller"},
    )
    second = await resolver.resolve(
        url,
        principal_key="tenant-a\0user-a",
        headers={"authorization": "Bearer caller"},
    )

    assert first == second
    assert first.skills == ("code",)
    assert first.skill_details[0].name == "Code"
    assert first.skill_details[0].description == "Performs code"
    assert first.skill_details[0].tags == ["engineering"]
    assert first.signature_verified is None
    assert first.security_schemes["bearer"]["httpAuthSecurityScheme"]["scheme"] == "bearer"
    assert first.security_requirements == ({"schemes": {"bearer": {}}},)
    assert calls[0].headers["authorization"] == "Bearer caller"


@pytest.mark.asyncio
async def test_http_resolver_only_forwards_auth_to_explicitly_trusted_origins() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_card_payload())

    resolver = HttpAgentCardResolver(
        timeout_seconds=1.0,
        default_cache_ttl_seconds=5.0,
        signature_algorithms=["RS256"],
        authenticated_card_origins=["https://trusted.example.test"],
        transport=httpx.MockTransport(handler),
    )

    await resolver.resolve(
        "https://untrusted.example.test/.well-known/agent-card.json",
        principal_key="tenant-a\0user-a",
        headers={
            "authorization": "Bearer caller",
            "x-auth-user-id": "user-a",
        },
    )

    assert "authorization" not in calls[0].headers
    assert "x-auth-user-id" not in calls[0].headers


@pytest.mark.asyncio
async def test_http_resolver_isolates_principals_and_honors_no_store() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json=_card_payload(),
            headers={"ETag": '"private-card"', "Cache-Control": "private, no-store"},
        )

    resolver = HttpAgentCardResolver(
        timeout_seconds=1.0,
        default_cache_ttl_seconds=60.0,
        signature_algorithms=["RS256"],
        transport=httpx.MockTransport(handler),
    )
    url = "https://agent.example.test/.well-known/agent-card.json"

    await resolver.resolve(url, principal_key="tenant-a\0user-a", headers={})
    await resolver.resolve(url, principal_key="tenant-a\0user-a", headers={})
    await resolver.resolve(url, principal_key="tenant-a\0user-b", headers={})

    assert len(calls) == 3
    assert all("if-none-match" not in request.headers for request in calls)


@pytest.mark.asyncio
async def test_http_resolver_verifies_signed_agent_card() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk["kid"] = "agent-key"
    card = HttpAgentCardResolver._parse_card(_card_payload())
    signed: AgentCard = create_agent_card_signer(
        private_pem,
        {
            "alg": "RS256",
            "kid": "agent-key",
            "jku": "https://agent.example.test/jwks.json",
            "typ": "JOSE",
        },
    )(card)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/jwks.json":
            return httpx.Response(200, json={"keys": [public_jwk]})
        return httpx.Response(200, json=MessageToDict(signed))

    resolver = HttpAgentCardResolver(
        timeout_seconds=1.0,
        default_cache_ttl_seconds=5.0,
        signature_algorithms=["RS256"],
        transport=httpx.MockTransport(handler),
    )

    resolved = await resolver.resolve(
        "https://agent.example.test/.well-known/agent-card.json",
        principal_key="tenant-a\0user-a",
        headers={},
    )

    assert resolved.signature_verified is True
    assert resolved.signature_key_ids == ("agent-key",)
    assert len(resolved.signature_key_fingerprints) == 1

    unsigned_resolver = HttpAgentCardResolver(
        timeout_seconds=1.0,
        default_cache_ttl_seconds=5.0,
        signature_algorithms=["RS256"],
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=_card_payload())),
    )
    unsigned = await unsigned_resolver.resolve(
        "https://unsigned.example.test/.well-known/agent-card.json",
        principal_key="tenant-a\0user-a",
        headers={},
    )
    assert resolved.card_hash == unsigned.card_hash


@pytest.mark.asyncio
async def test_http_resolver_rejects_invalid_or_credential_bearing_cards() -> None:
    resolver = HttpAgentCardResolver(
        timeout_seconds=1.0,
        default_cache_ttl_seconds=5.0,
        signature_algorithms=["RS256"],
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"name": "incomplete"})
        ),
    )

    with pytest.raises(AgentCardResolutionError, match="must not embed credentials"):
        await resolver.resolve(
            "https://user:secret@agent.example.test/card",
            principal_key="principal",
            headers={},
        )
    with pytest.raises(AgentCardResolutionError, match="cannot use query strings"):
        await resolver.resolve(
            "https://agent.example.test/card?token=secret",
            principal_key="principal",
            headers={},
        )
    with pytest.raises(AgentCardResolutionError, match="missing required fields"):
        await resolver.resolve(
            "https://agent.example.test/card",
            principal_key="principal",
            headers={},
        )
    with pytest.raises(ValueError, match="Unsupported Agent Card signature algorithm"):
        HttpAgentCardResolver(
            timeout_seconds=1.0,
            default_cache_ttl_seconds=5.0,
            signature_algorithms=["none"],
        )
