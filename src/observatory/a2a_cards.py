"""HTTP adapter for resolving and validating standard A2A Agent Cards."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
from a2a.client.card_resolver import parse_agent_card
from a2a.types import AgentCard
from a2a.utils.signing import create_signature_verifier
from cryptography.hazmat.primitives import serialization
from google.protobuf.json_format import MessageToDict, ParseError
from jwt.api_jwk import PyJWK
from jwt.utils import base64url_decode

from niuu.domain.agent_directory import AgentInterface, AgentSkill
from niuu.ports.agent_cards import (
    AgentCardResolutionError,
    AgentCardResolverPort,
    ResolvedAgentCard,
)

_MAX_AGE_PATTERN = re.compile(r"(?:^|,)\s*max-age=(\d+)(?:\s*(?:,|$))", re.IGNORECASE)
_SUPPORTED_SIGNATURE_ALGORITHMS = frozenset({"ES256", "ES384", "RS256", "RS384", "PS256", "EdDSA"})


@dataclass(frozen=True)
class _CardCacheEntry:
    card: ResolvedAgentCard
    etag: str
    expires_at: float


def _validate_http_url(
    value: str,
    *,
    require_https: bool = False,
    allow_query: bool = False,
) -> str:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        parsed.port
    except ValueError as exc:
        raise AgentCardResolutionError("Agent Card URL is invalid") from exc
    allowed_schemes = {"https"} if require_https else {"http", "https"}
    if parsed.scheme.lower() not in allowed_schemes or not hostname:
        requirement = "absolute HTTPS" if require_https else "absolute HTTP(S)"
        raise AgentCardResolutionError(f"Agent Card URL must be an {requirement} URL")
    if parsed.username or parsed.password:
        raise AgentCardResolutionError("Agent Card URLs must not embed credentials")
    if parsed.fragment or (parsed.query and not allow_query):
        raise AgentCardResolutionError(
            "Agent Card and signature-key URLs cannot use query strings or fragments"
        )
    return value


def _origin(value: str) -> str:
    parsed = urlsplit(_validate_http_url(value))
    port = parsed.port
    hostname = parsed.hostname or ""
    host = f"[{hostname}]" if ":" in hostname else hostname
    default_port = 443 if parsed.scheme.lower() == "https" else 80
    authority = host if port in {None, default_port} else f"{host}:{port}"
    return f"{parsed.scheme.lower()}://{authority}"


def _key_fingerprint(key: PyJWK) -> str:
    """Return a stable fingerprint of verified asymmetric public-key material."""
    try:
        encoded = key.key.public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise AgentCardResolutionError(
            "JWKS signature key is not an asymmetric public key"
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


def _cache_ttl(response: httpx.Response, default_ttl_seconds: float) -> float | None:
    cache_control = response.headers.get("cache-control", "").casefold()
    directives = {item.strip().split("=", 1)[0] for item in cache_control.split(",")}
    if "no-store" in directives:
        return None
    if "no-cache" in directives:
        return 0.0
    match = _MAX_AGE_PATTERN.search(cache_control)
    ttl = default_ttl_seconds if match is None else float(match.group(1))
    try:
        age = max(float(response.headers.get("age", "0")), 0.0)
    except ValueError:
        age = 0.0
    return max(ttl - age, 0.0)


def _required_card_fields(card: AgentCard) -> list[str]:
    missing: list[str] = []
    if not card.name.strip():
        missing.append("name")
    if not card.description.strip():
        missing.append("description")
    if not card.version.strip():
        missing.append("version")
    if not card.supported_interfaces:
        missing.append("supportedInterfaces")
    if not card.HasField("capabilities"):
        missing.append("capabilities")
    if not card.default_input_modes:
        missing.append("defaultInputModes")
    if not card.default_output_modes:
        missing.append("defaultOutputModes")
    if not card.skills:
        missing.append("skills")
    return missing


def _without_empty_values(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned = {
            key: cleaned_value
            for key, item in value.items()
            if (cleaned_value := _without_empty_values(item)) is not None
        }
        return cleaned or None
    if isinstance(value, list):
        cleaned = [
            cleaned_value
            for item in value
            if (cleaned_value := _without_empty_values(item)) is not None
        ]
        return cleaned or None
    if value == "":
        return None
    return value


def _canonical_card_hash(card: AgentCard) -> str:
    """Hash the same signature-free semantic payload used by the official SDK signer."""
    card_payload = MessageToDict(card)
    card_payload.pop("signatures", None)
    canonical_payload = json.dumps(
        _without_empty_values(card_payload),
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()


def _protected_headers(card: AgentCard) -> list[dict[str, Any]]:
    headers: list[dict[str, Any]] = []
    for signature in card.signatures:
        try:
            payload = base64url_decode(signature.protected.encode("utf-8")).decode("utf-8")
            header = json.loads(payload)
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            raise AgentCardResolutionError("Agent Card contains an invalid JWS header") from exc
        if not isinstance(header, dict):
            raise AgentCardResolutionError("Agent Card contains an invalid JWS header")
        headers.append(header)
    return headers


class HttpAgentCardResolver(AgentCardResolverPort):
    """Resolve cards with caller-isolated HTTP caching and signature verification."""

    def __init__(
        self,
        *,
        timeout_seconds: float,
        default_cache_ttl_seconds: float,
        signature_algorithms: list[str],
        authenticated_card_origins: list[str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._default_cache_ttl_seconds = default_cache_ttl_seconds
        unsupported = set(signature_algorithms) - _SUPPORTED_SIGNATURE_ALGORITHMS
        if unsupported:
            raise ValueError(
                f"Unsupported Agent Card signature algorithm(s): {', '.join(sorted(unsupported))}"
            )
        self._signature_algorithms = list(signature_algorithms)
        self._authenticated_card_origins = frozenset(
            _origin(value.strip()) for value in authenticated_card_origins or ()
        )
        self._transport = transport
        self._cache: dict[tuple[str, str], _CardCacheEntry] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    async def resolve(
        self,
        card_url: str,
        *,
        principal_key: str,
        headers: Mapping[str, str],
    ) -> ResolvedAgentCard:
        """Resolve a standard card without sharing restricted cards between callers."""
        card_url = _validate_http_url(card_url.strip())
        cache_key = (principal_key, card_url)
        cached = self._cache.get(cache_key)
        now = time.monotonic()
        if cached is not None and cached.expires_at > now:
            return cached.card

        lock = self._locks.setdefault(cache_key, asyncio.Lock())
        async with lock:
            cached = self._cache.get(cache_key)
            now = time.monotonic()
            if cached is not None and cached.expires_at > now:
                return cached.card

            request_headers = (
                dict(headers) if _origin(card_url) in self._authenticated_card_origins else {}
            )
            request_headers.setdefault("Accept", "application/a2a+json, application/json")
            if cached is not None and cached.etag:
                request_headers["If-None-Match"] = cached.etag

            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout_seconds,
                    follow_redirects=False,
                    transport=self._transport,
                ) as client:
                    response = await client.get(card_url, headers=request_headers)
                    if response.status_code == httpx.codes.NOT_MODIFIED and cached is not None:
                        ttl = _cache_ttl(response, self._default_cache_ttl_seconds)
                        if ttl is None:
                            self._cache.pop(cache_key, None)
                        else:
                            self._cache[cache_key] = _CardCacheEntry(
                                card=cached.card,
                                etag=cached.etag,
                                expires_at=now + ttl,
                            )
                        return cached.card
                    response.raise_for_status()
                    payload = response.json()
                    card = self._parse_card(payload)
                    signature_verified, key_ids, key_fingerprints = await self._verify_signatures(
                        card,
                        client=client,
                    )
            except AgentCardResolutionError:
                raise
            except (httpx.HTTPError, json.JSONDecodeError, ParseError, ValueError) as exc:
                raise AgentCardResolutionError("Unable to resolve Agent Card") from exc

            resolved = self._to_resolved(
                card,
                signature_verified=signature_verified,
                signature_key_ids=key_ids,
                signature_key_fingerprints=key_fingerprints,
            )
            ttl = _cache_ttl(response, self._default_cache_ttl_seconds)
            if ttl is not None:
                self._cache[cache_key] = _CardCacheEntry(
                    card=resolved,
                    etag=response.headers.get("etag", ""),
                    expires_at=now + ttl,
                )
            return resolved

    @staticmethod
    def _parse_card(payload: Any) -> AgentCard:
        if not isinstance(payload, dict):
            raise AgentCardResolutionError("Agent Card response must be a JSON object")
        card = parse_agent_card(deepcopy(payload))
        missing = _required_card_fields(card)
        if missing:
            raise AgentCardResolutionError(
                f"Agent Card is missing required fields: {', '.join(missing)}"
            )
        for interface in card.supported_interfaces:
            _validate_http_url(interface.url, allow_query=True)
            if not interface.protocol_binding.strip() or not interface.protocol_version.strip():
                raise AgentCardResolutionError(
                    "Agent Card interfaces require protocolBinding and protocolVersion"
                )
        for skill in card.skills:
            if not skill.id.strip() or not skill.name.strip() or not skill.description.strip():
                raise AgentCardResolutionError(
                    "Agent Card skills require id, name, and description"
                )
            if not skill.tags:
                raise AgentCardResolutionError("Agent Card skills require at least one tag")
        return card

    async def _verify_signatures(
        self,
        card: AgentCard,
        *,
        client: httpx.AsyncClient,
    ) -> tuple[bool | None, tuple[str, ...], tuple[str, ...]]:
        if not card.signatures:
            return None, (), ()

        protected_headers = _protected_headers(card)
        keys: dict[tuple[str, str], PyJWK] = {}
        key_ids: list[str] = []
        key_fingerprints: list[str] = []
        for header in protected_headers:
            key_id = str(header.get("kid") or "").strip()
            jwks_url = str(header.get("jku") or "").strip()
            algorithm = str(header.get("alg") or "").strip()
            if not key_id or not jwks_url or not algorithm:
                raise AgentCardResolutionError(
                    "Signed Agent Cards require alg, kid, and jku protected headers"
                )
            if algorithm not in self._signature_algorithms:
                raise AgentCardResolutionError(
                    f"Agent Card signature algorithm is not allowed: {algorithm}"
                )
            _validate_http_url(jwks_url, require_https=True)
            key_ids.append(key_id)
            if (key_id, jwks_url) in keys:
                continue
            response = await client.get(jwks_url, headers={"Accept": "application/json"})
            response.raise_for_status()
            payload = response.json()
            jwks = payload.get("keys") if isinstance(payload, dict) else None
            if not isinstance(jwks, list):
                raise AgentCardResolutionError("Agent Card signature jku did not return a JWKS")
            matching = next(
                (
                    item
                    for item in jwks
                    if isinstance(item, dict) and str(item.get("kid") or "") == key_id
                ),
                None,
            )
            if matching is None:
                raise AgentCardResolutionError(f"JWKS does not contain signature key {key_id}")
            try:
                key = PyJWK.from_dict(matching)
            except Exception as exc:
                raise AgentCardResolutionError("JWKS contains an invalid signature key") from exc
            keys[(key_id, jwks_url)] = key
            key_fingerprints.append(_key_fingerprint(key))

        verifier = create_signature_verifier(
            lambda kid, jku: keys[(str(kid or ""), str(jku or ""))],
            algorithms=self._signature_algorithms,
        )
        try:
            verifier(card)
        except Exception as exc:
            raise AgentCardResolutionError("Agent Card signature verification failed") from exc
        return (
            True,
            tuple(dict.fromkeys(key_ids)),
            tuple(sorted(set(key_fingerprints))),
        )

    @staticmethod
    def _to_resolved(
        card: AgentCard,
        *,
        signature_verified: bool | None,
        signature_key_ids: tuple[str, ...],
        signature_key_fingerprints: tuple[str, ...],
    ) -> ResolvedAgentCard:
        card_payload = MessageToDict(card)
        capabilities = card_payload.get("capabilities", {})
        security_schemes = card_payload.get("securitySchemes", {})
        security_requirements = card_payload.get("securityRequirements", [])
        raw_skills = card_payload.get("skills", [])
        skill_payloads = raw_skills if isinstance(raw_skills, list) else []
        skill_details: list[AgentSkill] = []
        for skill in card.skills:
            raw = next(
                (
                    item
                    for item in skill_payloads
                    if isinstance(item, dict) and str(item.get("id") or "") == skill.id
                ),
                {},
            )
            examples = raw.get("examples") if isinstance(raw, dict) else []
            input_modes = raw.get("inputModes") if isinstance(raw, dict) else []
            output_modes = raw.get("outputModes") if isinstance(raw, dict) else []
            requirements = raw.get("securityRequirements") if isinstance(raw, dict) else []
            skill_details.append(
                AgentSkill(
                    id=skill.id,
                    name=skill.name,
                    description=skill.description,
                    tags=list(skill.tags),
                    examples=(
                        [str(item) for item in examples if str(item).strip()]
                        if isinstance(examples, list)
                        else []
                    ),
                    inputModes=(
                        [str(item) for item in input_modes if str(item).strip()]
                        if isinstance(input_modes, list)
                        else []
                    ),
                    outputModes=(
                        [str(item) for item in output_modes if str(item).strip()]
                        if isinstance(output_modes, list)
                        else []
                    ),
                    securityRequirements=(
                        [item for item in requirements if isinstance(item, dict)]
                        if isinstance(requirements, list)
                        else []
                    ),
                )
            )
        return ResolvedAgentCard(
            name=card.name,
            description=card.description,
            version=card.version,
            skills=tuple(skill.id for skill in card.skills),
            tags=tuple(dict.fromkeys(tag for skill in card.skills for tag in skill.tags)),
            default_input_modes=tuple(card.default_input_modes),
            default_output_modes=tuple(card.default_output_modes),
            supported_interfaces=tuple(
                AgentInterface(
                    url=interface.url,
                    protocolBinding=interface.protocol_binding,
                    protocolVersion=interface.protocol_version,
                    tenant=interface.tenant,
                )
                for interface in card.supported_interfaces
            ),
            skill_details=tuple(skill_details),
            capabilities=capabilities if isinstance(capabilities, dict) else {},
            security_schemes=(security_schemes if isinstance(security_schemes, dict) else {}),
            security_requirements=(
                tuple(item for item in security_requirements if isinstance(item, dict))
                if isinstance(security_requirements, list)
                else ()
            ),
            card_hash=_canonical_card_hash(card),
            signature_verified=signature_verified,
            signature_key_ids=signature_key_ids,
            signature_key_fingerprints=signature_key_fingerprints,
        )
