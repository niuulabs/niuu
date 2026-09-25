"""Tests for the in-process JWKS bearer adapters (hosts without Envoy)."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from unittest.mock import AsyncMock

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import Response
from jwt.algorithms import RSAAlgorithm

from identity.adapters.jwks import JwksBearerAuthenticationAdapter, JwksIdentityAdapter
from identity.models import Principal, User, UserStatus
from identity.ports import AuthorizationEvaluationError
from niuu.ports.identity import InvalidTokenError

ISSUER = "https://issuer.example.com/realms/volundr"
AUDIENCE = "volundr-api"
JWKS_URI = f"{ISSUER}/protocol/openid-connect/certs"
KID = "test-key-1"


def _generate_keypair() -> tuple[rsa.RSAPrivateKey, dict]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": KID, "use": "sig", "alg": "RS256"})
    return private_key, jwk


PRIVATE_KEY, PUBLIC_JWK = _generate_keypair()
OTHER_PRIVATE_KEY, _ = _generate_keypair()


def _make_token(
    *,
    key=PRIVATE_KEY,
    kid: str | None = KID,
    issuer: str = ISSUER,
    audience: str | list[str] = AUDIENCE,
    sub: str = "user-123",
    email: str = "alice@example.com",
    tenant_id: str | None = "tenant-1",
    roles: list[str] | str | None = None,
    exp_offset: int = 300,
    nbf_offset: int | None = None,
    extra_claims: dict | None = None,
) -> str:
    now = int(time.time())
    claims: dict = {
        "sub": sub,
        "iss": issuer,
        "aud": audience,
        "exp": now + exp_offset,
        "iat": now,
        "email": email,
    }
    if tenant_id is not None:
        claims["tenant_id"] = tenant_id
    if roles is not None:
        claims["resource_access"] = {"volundr": {"roles": roles}}
    if nbf_offset is not None:
        claims["nbf"] = now + nbf_offset
    if extra_claims:
        claims.update(extra_claims)
    headers = {"kid": kid} if kid else {}
    return jwt.encode(claims, key, algorithm="RS256", headers=headers)


def _make_adapter(**overrides) -> JwksBearerAuthenticationAdapter:
    kwargs = {
        "issuers": [{"issuer": ISSUER, "audiences": [AUDIENCE], "jwks_uri": JWKS_URI}],
    }
    kwargs.update(overrides)
    return JwksBearerAuthenticationAdapter(**kwargs)


def _mock_jwks(keys: list[dict] | None = None):
    body = {"keys": keys if keys is not None else [PUBLIC_JWK]}
    return respx.get(JWKS_URI).mock(return_value=Response(200, json=body))


class TestJwksBearerAuthenticationAdapter:
    def test_requires_at_least_one_issuer(self) -> None:
        with pytest.raises(ValueError, match="auth.oidc.issuers"):
            JwksBearerAuthenticationAdapter(issuers=[])

    def test_requires_audiences_per_issuer(self) -> None:
        with pytest.raises(ValueError, match="audience"):
            JwksBearerAuthenticationAdapter(issuers=[{"issuer": ISSUER, "audiences": []}])

    async def test_missing_authorization_header_raises(self) -> None:
        adapter = _make_adapter()
        with pytest.raises(InvalidTokenError, match="Missing bearer token"):
            await adapter.validate_headers({})

    async def test_non_bearer_authorization_raises(self) -> None:
        adapter = _make_adapter()
        with pytest.raises(InvalidTokenError, match="Missing bearer token"):
            await adapter.validate_headers({"authorization": "Basic abc"})

    @respx.mock
    async def test_valid_token_returns_principal(self) -> None:
        _mock_jwks()
        adapter = _make_adapter()
        token = _make_token(roles=["admin", "developer"])

        principal = await adapter.validate_headers({"authorization": f"Bearer {token}"})

        assert isinstance(principal, Principal)
        assert principal.user_id == "user-123"
        assert principal.email == "alice@example.com"
        assert principal.tenant_id == "tenant-1"
        assert principal.roles == ["admin", "developer"]

    @respx.mock
    async def test_role_mapping_is_applied(self) -> None:
        _mock_jwks()
        adapter = _make_adapter(role_mapping={"admin": "volundr:admin"})
        token = _make_token(roles=["admin"])

        principal = await adapter.validate_headers({"authorization": f"Bearer {token}"})

        assert principal.roles == ["volundr:admin"]

    @respx.mock
    async def test_wrong_issuer_rejected(self) -> None:
        _mock_jwks()
        adapter = _make_adapter()
        token = _make_token(issuer="https://untrusted.example.com")

        with pytest.raises(InvalidTokenError, match="Untrusted token issuer"):
            await adapter.validate_headers({"authorization": f"Bearer {token}"})

    @respx.mock
    async def test_wrong_audience_rejected(self) -> None:
        _mock_jwks()
        adapter = _make_adapter()
        token = _make_token(audience="some-other-service")

        with pytest.raises(InvalidTokenError, match="failed verification"):
            await adapter.validate_headers({"authorization": f"Bearer {token}"})

    @respx.mock
    async def test_expired_token_rejected(self) -> None:
        _mock_jwks()
        adapter = _make_adapter()
        token = _make_token(exp_offset=-60)

        with pytest.raises(InvalidTokenError, match="failed verification"):
            await adapter.validate_headers({"authorization": f"Bearer {token}"})

    @respx.mock
    async def test_not_yet_valid_nbf_rejected(self) -> None:
        _mock_jwks()
        adapter = _make_adapter()
        token = _make_token(nbf_offset=3600)

        with pytest.raises(InvalidTokenError, match="failed verification"):
            await adapter.validate_headers({"authorization": f"Bearer {token}"})

    @respx.mock
    async def test_nbf_within_leeway_is_accepted(self) -> None:
        _mock_jwks()
        adapter = _make_adapter(clock_leeway_seconds=120)
        token = _make_token(nbf_offset=60)

        principal = await adapter.validate_headers({"authorization": f"Bearer {token}"})
        assert principal.user_id == "user-123"

    @respx.mock
    async def test_bad_signature_rejected(self) -> None:
        _mock_jwks()
        adapter = _make_adapter()
        token = _make_token(key=OTHER_PRIVATE_KEY)

        with pytest.raises(InvalidTokenError, match="failed verification"):
            await adapter.validate_headers({"authorization": f"Bearer {token}"})

    @respx.mock
    async def test_unknown_kid_triggers_jwks_refresh(self) -> None:
        new_key, new_jwk = _generate_keypair()
        new_jwk["kid"] = "rotated-key"
        route = respx.get(JWKS_URI)
        # First fetch (populating the cache) returns only the old key; the
        # rotated key only appears once the adapter forces a refresh.
        route.side_effect = [
            Response(200, json={"keys": [PUBLIC_JWK]}),
            Response(200, json={"keys": [PUBLIC_JWK, new_jwk]}),
        ]
        adapter = _make_adapter()
        # Prime the cache with the old keyset.
        await adapter._keys_for_issuer(next(iter(adapter._issuers.values())))

        token = _make_token(key=new_key, kid="rotated-key")
        principal = await adapter.validate_headers({"authorization": f"Bearer {token}"})

        assert principal.user_id == "user-123"
        assert route.call_count == 2

    @respx.mock
    async def test_still_unknown_kid_after_refresh_raises(self) -> None:
        _mock_jwks()
        adapter = _make_adapter()
        token = _make_token(kid="never-published")

        with pytest.raises(InvalidTokenError, match="No matching JWKS key"):
            await adapter.validate_headers({"authorization": f"Bearer {token}"})

    @respx.mock
    async def test_spoofed_x_auth_headers_are_ignored(self) -> None:
        _mock_jwks()
        adapter = _make_adapter()
        token = _make_token(sub="real-user")

        principal = await adapter.validate_headers(
            {
                "authorization": f"Bearer {token}",
                "x-auth-user-id": "attacker-supplied-admin",
                "x-auth-roles": "volundr:admin",
                "x-auth-tenant": "attacker-tenant",
            }
        )

        assert principal.user_id == "real-user"
        assert principal.tenant_id == "tenant-1"

    @respx.mock
    async def test_ws_query_param_token_merged_into_authorization(self) -> None:
        """Mirrors how identity.adapters.http_auth.extract_principal forwards a
        WebSocket ``?token=`` query param into a synthetic Authorization header
        before calling validate_headers — the adapter itself is transport-agnostic.
        """
        _mock_jwks()
        adapter = _make_adapter()
        token = _make_token()

        headers: dict[str, str] = {}
        query_token = token
        if "authorization" not in headers and query_token:
            headers["authorization"] = f"Bearer {query_token}"

        principal = await adapter.validate_headers(headers)
        assert principal.user_id == "user-123"

    @respx.mock
    async def test_missing_user_id_claim_raises(self) -> None:
        _mock_jwks()
        adapter = _make_adapter(user_id_claim="preferred_username")
        token = _make_token()

        with pytest.raises(InvalidTokenError, match="preferred_username"):
            await adapter.validate_headers({"authorization": f"Bearer {token}"})

    @respx.mock
    async def test_missing_tenant_claim_defaults_to_empty(self) -> None:
        _mock_jwks()
        adapter = _make_adapter()
        token = _make_token(tenant_id=None)

        principal = await adapter.validate_headers({"authorization": f"Bearer {token}"})
        assert principal.tenant_id == ""

    @respx.mock
    async def test_discovers_jwks_uri_when_not_configured(self) -> None:
        discovery = respx.get(f"{ISSUER}/.well-known/openid-configuration").mock(
            return_value=Response(200, json={"issuer": ISSUER, "jwks_uri": JWKS_URI})
        )
        _mock_jwks()
        adapter = JwksBearerAuthenticationAdapter(
            issuers=[{"issuer": ISSUER, "audiences": [AUDIENCE]}]
        )
        token = _make_token()

        principal = await adapter.validate_headers({"authorization": f"Bearer {token}"})

        assert principal.user_id == "user-123"
        assert discovery.called


class TestJwksIdentityAdapter:
    def _adapter(self, user_repo) -> JwksIdentityAdapter:
        return JwksIdentityAdapter(
            issuers=[{"issuer": ISSUER, "audiences": [AUDIENCE], "jwks_uri": JWKS_URI}],
            user_repository=user_repo,
            role_mapping={"admin": "volundr:admin"},
        )

    @respx.mock
    async def test_verified_claims_provision_a_user(self) -> None:
        _mock_jwks()
        user_repo = AsyncMock()
        user_repo.get.return_value = None
        user_repo.create.return_value = User(
            id="user-123", email="alice@example.com", status=UserStatus.PROVISIONING
        )
        adapter = self._adapter(user_repo)
        token = _make_token(roles=["admin"])

        principal = await adapter.validate_headers({"authorization": f"Bearer {token}"})
        await adapter.get_or_provision_user(principal)

        assert principal.user_id == "user-123"
        assert principal.roles == ["volundr:admin"]
        user_repo.create.assert_called_once()

    @respx.mock
    async def test_spoofed_headers_cannot_reach_provisioning_pipeline(self) -> None:
        _mock_jwks()
        user_repo = AsyncMock()
        user_repo.get.return_value = None
        user_repo.create.return_value = User(
            id="real-user", email="alice@example.com", status=UserStatus.PROVISIONING
        )
        adapter = self._adapter(user_repo)
        token = _make_token(sub="real-user")

        principal = await adapter.validate_headers(
            {
                "authorization": f"Bearer {token}",
                "x-auth-user-id": "attacker-admin",
            }
        )
        await adapter.get_or_provision_user(principal)

        assert principal.user_id == "real-user"
        provisioned_id = user_repo.create.call_args.args[0].id
        assert provisioned_id == "real-user"

    async def test_validate_token_rejects_raw_tokens(self) -> None:
        adapter = self._adapter(AsyncMock())
        with pytest.raises(InvalidTokenError, match="requires headers"):
            await adapter.validate_token("some-token")

    async def test_validate_token_empty_raises(self) -> None:
        adapter = self._adapter(AsyncMock())
        with pytest.raises(InvalidTokenError):
            await adapter.validate_token("")


class TestHttpsEnforcement:
    def test_http_issuer_rejected(self) -> None:
        with pytest.raises(ValueError, match="HTTPS"):
            JwksBearerAuthenticationAdapter(
                issuers=[{"issuer": "http://issuer.example.com", "audiences": [AUDIENCE]}]
            )

    def test_http_jwks_uri_rejected(self) -> None:
        with pytest.raises(ValueError, match="HTTPS"):
            JwksBearerAuthenticationAdapter(
                issuers=[
                    {
                        "issuer": ISSUER,
                        "audiences": [AUDIENCE],
                        "jwks_uri": "http://issuer.example.com/certs",
                    }
                ]
            )

    def test_localhost_http_issuer_allowed(self) -> None:
        JwksBearerAuthenticationAdapter(
            issuers=[{"issuer": "http://localhost:8080/realms/dev", "audiences": [AUDIENCE]}]
        )

    def test_127_0_0_1_http_jwks_uri_allowed(self) -> None:
        JwksBearerAuthenticationAdapter(
            issuers=[
                {
                    "issuer": "http://127.0.0.1:8080/realms/dev",
                    "audiences": [AUDIENCE],
                    "jwks_uri": "http://127.0.0.1:8080/certs",
                }
            ]
        )

    @respx.mock
    async def test_discovery_document_issuer_mismatch_rejected(self) -> None:
        respx.get(f"{ISSUER}/.well-known/openid-configuration").mock(
            return_value=Response(
                200, json={"issuer": "https://someone-else.example", "jwks_uri": JWKS_URI}
            )
        )
        adapter = JwksBearerAuthenticationAdapter(
            issuers=[{"issuer": ISSUER, "audiences": [AUDIENCE]}]
        )
        token = _make_token()
        with pytest.raises(AuthorizationEvaluationError, match="mismatched authority"):
            await adapter.validate_headers({"authorization": f"Bearer {token}"})

    @respx.mock
    async def test_discovered_jwks_uri_must_also_be_https(self) -> None:
        respx.get(f"{ISSUER}/.well-known/openid-configuration").mock(
            return_value=Response(
                200, json={"issuer": ISSUER, "jwks_uri": "http://attacker.example/certs"}
            )
        )
        adapter = JwksBearerAuthenticationAdapter(
            issuers=[{"issuer": ISSUER, "audiences": [AUDIENCE]}]
        )
        token = _make_token()
        with pytest.raises(AuthorizationEvaluationError, match="HTTPS"):
            await adapter.validate_headers({"authorization": f"Bearer {token}"})


class TestKeyTypeAndAlgorithmRejection:
    @respx.mock
    async def test_symmetric_oct_key_never_trusted(self) -> None:
        """A JWKS response is public; a symmetric key published in it protects
        nothing and must never be treated as a trusted verification key."""
        oct_jwk = {
            "kty": "oct",
            "kid": KID,
            "k": base64.urlsafe_b64encode(b"shhh").decode().rstrip("="),
        }
        _mock_jwks(keys=[oct_jwk])
        adapter = _make_adapter()
        token = _make_token()

        with pytest.raises(InvalidTokenError, match="No matching JWKS key"):
            await adapter.validate_headers({"authorization": f"Bearer {token}"})

    @respx.mock
    async def test_encryption_only_key_never_trusted(self) -> None:
        enc_jwk = dict(PUBLIC_JWK)
        enc_jwk["use"] = "enc"
        _mock_jwks(keys=[enc_jwk])
        adapter = _make_adapter()
        token = _make_token()

        with pytest.raises(InvalidTokenError, match="No matching JWKS key"):
            await adapter.validate_headers({"authorization": f"Bearer {token}"})

    @respx.mock
    async def test_algorithm_confusion_hs256_with_rsa_public_key_rejected(self) -> None:
        """An attacker who knows the RSA public key can try signing an HS256
        token using that key's PEM bytes as the HMAC secret. The verifier
        must reject it: it always asks jwt.decode to verify with the
        algorithm the *published JWK* declares (RS256), never the algorithm
        the untrusted token's own header claims.

        PyJWT's own jwt.encode() already refuses to sign HS256 with an
        asymmetric key/PEM as a deliberate safety net, so this test forges
        the token by hand (raw HMAC over the signing input) to prove the
        *verification* side is independently safe rather than relying on
        that encode-time guard.
        """
        import base64 as _b64
        import hashlib
        import hmac

        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

        _mock_jwks()
        adapter = _make_adapter()
        public_pem = PRIVATE_KEY.public_key().public_bytes(
            encoding=Encoding.PEM, format=PublicFormat.SubjectPublicKeyInfo
        )

        def _b64url(data: bytes) -> bytes:
            return _b64.urlsafe_b64encode(data).rstrip(b"=")

        header = _b64url(json.dumps({"alg": "HS256", "kid": KID}, separators=(",", ":")).encode())
        payload = _b64url(
            json.dumps(
                {"sub": "attacker", "iss": ISSUER, "aud": AUDIENCE, "exp": int(time.time()) + 300},
                separators=(",", ":"),
            ).encode()
        )
        signing_input = header + b"." + payload
        signature = hmac.new(public_pem, signing_input, hashlib.sha256).digest()
        forged = (signing_input + b"." + _b64url(signature)).decode()

        with pytest.raises(InvalidTokenError, match="failed verification"):
            await adapter.validate_headers({"authorization": f"Bearer {forged}"})

    @respx.mock
    async def test_alg_none_token_rejected(self) -> None:
        _mock_jwks()
        adapter = _make_adapter()
        header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "kid": KID}).encode()).rstrip(
            b"="
        )
        payload = base64.urlsafe_b64encode(
            json.dumps(
                {
                    "sub": "attacker",
                    "iss": ISSUER,
                    "aud": AUDIENCE,
                    "exp": int(time.time()) + 300,
                }
            ).encode()
        ).rstrip(b"=")
        forged = (header + b"." + payload + b".").decode()
        with pytest.raises(InvalidTokenError):
            await adapter.validate_headers({"authorization": f"Bearer {forged}"})


class TestRefreshAmplificationGuard:
    @respx.mock
    async def test_many_forged_kids_cause_bounded_jwks_fetches(self) -> None:
        """The review's probe: many tokens with an unknown/missing kid must
        not each force their own JWKS fetch — that is a remotely triggerable
        amplification against the issuer's JWKS endpoint."""
        route = _mock_jwks()
        adapter = _make_adapter(min_refresh_interval_seconds=3600)

        for i in range(50):
            token = _make_token(kid=f"never-published-{i}")
            with pytest.raises(InvalidTokenError, match="No matching JWKS key"):
                await adapter.validate_headers({"authorization": f"Bearer {token}"})

        # One routine fetch plus at most one forced refresh — not 51.
        assert route.call_count <= 2

    @respx.mock
    async def test_concurrent_requests_single_flight_the_refresh(self) -> None:
        route = _mock_jwks()
        adapter = _make_adapter()
        token = _make_token()
        # Prime the cache so the first (uncontended) fetch is out of the way,
        # isolating the single-flight behaviour of the *forced* refresh path.
        await adapter.validate_headers({"authorization": f"Bearer {token}"})
        assert route.call_count == 1

        unknown_tokens = [_make_token(kid=f"rotated-{i}") for i in range(10)]
        results = await asyncio.gather(
            *(adapter.validate_headers({"authorization": f"Bearer {t}"}) for t in unknown_tokens),
            return_exceptions=True,
        )
        assert all(isinstance(r, InvalidTokenError) for r in results)
        # A single concurrent burst of unknown kids collapses to one more
        # fetch, not one per waiter.
        assert route.call_count == 2


class TestNetworkFailuresRaiseAuthorizationEvaluationError:
    @respx.mock
    async def test_jwks_fetch_network_error(self) -> None:
        respx.get(JWKS_URI).mock(side_effect=httpx.ConnectError("connection refused"))
        adapter = _make_adapter()
        token = _make_token()
        with pytest.raises(AuthorizationEvaluationError, match="JWKS fetch"):
            await adapter.validate_headers({"authorization": f"Bearer {token}"})

    @respx.mock
    async def test_discovery_network_error(self) -> None:
        respx.get(f"{ISSUER}/.well-known/openid-configuration").mock(
            side_effect=httpx.ConnectError("connection refused")
        )
        adapter = JwksBearerAuthenticationAdapter(
            issuers=[{"issuer": ISSUER, "audiences": [AUDIENCE]}]
        )
        token = _make_token()
        with pytest.raises(AuthorizationEvaluationError, match="discovery"):
            await adapter.validate_headers({"authorization": f"Bearer {token}"})

    @respx.mock
    async def test_jwks_bad_json_wrapped(self) -> None:
        respx.get(JWKS_URI).mock(return_value=Response(200, text="not json"))
        adapter = _make_adapter()
        token = _make_token()
        with pytest.raises(AuthorizationEvaluationError, match="JWKS fetch"):
            await adapter.validate_headers({"authorization": f"Bearer {token}"})


class TestDiscoveredJwksUriCached:
    @respx.mock
    async def test_discovery_only_happens_once(self) -> None:
        discovery = respx.get(f"{ISSUER}/.well-known/openid-configuration").mock(
            return_value=Response(200, json={"issuer": ISSUER, "jwks_uri": JWKS_URI})
        )
        _mock_jwks()
        adapter = JwksBearerAuthenticationAdapter(
            issuers=[{"issuer": ISSUER, "audiences": [AUDIENCE]}],
            jwks_cache_ttl_seconds=0.001,
        )
        token1 = _make_token()
        await adapter.validate_headers({"authorization": f"Bearer {token1}"})
        assert discovery.call_count == 1

        # A second, routine (TTL-expired) fetch must reuse the resolved
        # jwks_uri instead of re-running discovery.
        token2 = _make_token(sub="user-456")
        await adapter.validate_headers({"authorization": f"Bearer {token2}"})
        assert discovery.call_count == 1


class TestRevalidateVerifiedPrincipal:
    """Covers the session-proxy re-check path: a principal this process
    already verified once, re-run through role-mapping/membership derivation
    with no fresh bearer token to check (there isn't one at that point)."""

    async def test_reapplies_role_mapping_without_a_token(self) -> None:
        user_repo = AsyncMock()
        adapter = JwksIdentityAdapter(
            issuers=[{"issuer": ISSUER, "audiences": [AUDIENCE], "jwks_uri": JWKS_URI}],
            user_repository=user_repo,
            role_mapping={"admin": "volundr:admin"},
        )
        principal = Principal(user_id="u1", email="a@b.com", tenant_id="t1", roles=["admin"])

        result = await adapter.revalidate_verified_principal(principal)

        assert result.user_id == "u1"
        assert result.roles == ["volundr:admin"]
