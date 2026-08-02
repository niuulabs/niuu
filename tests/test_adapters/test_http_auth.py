"""Tests for reusable outbound HTTP auth adapters."""

from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx
import pytest

from niuu.adapters.outbound.http_auth import (
    ClientCredentialsBearerTokenAuthAdapter,
    WorkloadIdentityBearerTokenAuthAdapter,
)


def test_client_credentials_adapter_mints_and_caches_bearer_token() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = parse_qs(request.content.decode())
        assert body["grant_type"] == ["client_credentials"]
        assert body["client_id"] == ["volundr"]
        assert body["client_secret"] == ["secret"]
        assert body["audience"] == ["volundr-api"]
        return httpx.Response(200, json={"access_token": "jwt-123", "expires_in": 300})

    adapter = ClientCredentialsBearerTokenAuthAdapter(
        token_url="https://keycloak.test/token",
        client_id="volundr",
        client_secret="secret",
        audience="volundr-api",
        transport=httpx.MockTransport(handler),
    )

    assert adapter.headers() == {"Authorization": "Bearer jwt-123"}
    assert adapter.headers() == {"Authorization": "Bearer jwt-123"}
    assert calls == 1


def test_client_credentials_adapter_reads_secret_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLIENT_SECRET", "env-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        body = parse_qs(request.content.decode())
        assert body["client_secret"] == ["env-secret"]
        return httpx.Response(200, json={"access_token": "jwt-456"})

    adapter = ClientCredentialsBearerTokenAuthAdapter(
        token_url="https://keycloak.test/token",
        client_id="volundr",
        client_secret_env="CLIENT_SECRET",
        transport=httpx.MockTransport(handler),
    )

    assert adapter.headers() == {"Authorization": "Bearer jwt-456"}


def test_client_credentials_adapter_requires_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_SECRET", raising=False)
    adapter = ClientCredentialsBearerTokenAuthAdapter(
        token_url="https://keycloak.test/token",
        client_id="volundr",
        client_secret_env="MISSING_SECRET",
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
    )

    with pytest.raises(RuntimeError, match="client secret"):
        adapter.headers()


def test_workload_identity_adapter_requests_build_scopes(tmp_path) -> None:
    proof_file = tmp_path / "token"
    proof_file.write_text("proof-jwt", encoding="utf-8")
    seen_bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_bodies.append(json.loads(request.content.decode()))
        return httpx.Response(200, json={"token": "scoped-jwt", "expires_in": 300})

    adapter = WorkloadIdentityBearerTokenAuthAdapter(
        exchange_url="https://volundr.test/api/v1/tokens/workload/exchange",
        token_file=str(proof_file),
        scopes=["forge:session:create"],
        transport=httpx.MockTransport(handler),
    )

    assert adapter.headers() == {"Authorization": "Bearer scoped-jwt"}
    assert seen_bodies[0]["token"] == "proof-jwt"
    assert seen_bodies[0]["scopes"] == ["forge:session:create"]


def test_workload_identity_adapter_omits_scopes_when_not_requested(tmp_path) -> None:
    proof_file = tmp_path / "token"
    proof_file.write_text("proof-jwt", encoding="utf-8")
    seen_bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_bodies.append(json.loads(request.content.decode()))
        return httpx.Response(200, json={"token": "plain-jwt", "expires_in": 300})

    adapter = WorkloadIdentityBearerTokenAuthAdapter(
        exchange_url="https://volundr.test/api/v1/tokens/workload/exchange",
        token_file=str(proof_file),
        transport=httpx.MockTransport(handler),
    )

    assert adapter.headers() == {"Authorization": "Bearer plain-jwt"}
    assert "scopes" not in seen_bodies[0]


def test_workload_identity_adapter_reloads_projected_token_after_rejection(tmp_path) -> None:
    proof_file = tmp_path / "token"
    proof_file.write_text("proof-jwt-1", encoding="utf-8")
    seen_proofs: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        proof = json.loads(request.content.decode())["token"]
        seen_proofs.append(proof)
        return httpx.Response(200, json={"token": f"exchanged-{proof}", "expires_in": 300})

    adapter = WorkloadIdentityBearerTokenAuthAdapter(
        exchange_url="https://volundr.test/api/v1/tokens/workload/exchange",
        token_file=str(proof_file),
        transport=httpx.MockTransport(handler),
    )

    assert adapter.headers() == {"Authorization": "Bearer exchanged-proof-jwt-1"}
    proof_file.write_text("proof-jwt-2", encoding="utf-8")
    assert adapter.invalidate() is True
    assert adapter.headers() == {"Authorization": "Bearer exchanged-proof-jwt-2"}
    assert seen_proofs == ["proof-jwt-1", "proof-jwt-2"]


def test_workload_identity_adapter_direct_kwargs_beat_env(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Config-first: direct token_file/exchange_url kwargs win over env-name fallbacks."""
    proof_file = tmp_path / "token"
    proof_file.write_text("proof-jwt", encoding="utf-8")
    env_proof_file = tmp_path / "env-token"
    env_proof_file.write_text("env-proof-jwt", encoding="utf-8")
    monkeypatch.setenv("NIUU_WORKLOAD_IDENTITY_TOKEN_FILE", str(env_proof_file))
    monkeypatch.setenv("NIUU_WORKLOAD_IDENTITY_EXCHANGE_URL", "https://env-host/exchange")
    seen: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), json.loads(request.content.decode())))
        return httpx.Response(200, json={"token": "direct-jwt", "expires_in": 300})

    adapter = WorkloadIdentityBearerTokenAuthAdapter(
        exchange_url="https://direct-host/exchange",
        token_file=str(proof_file),
        transport=httpx.MockTransport(handler),
    )

    assert adapter.headers() == {"Authorization": "Bearer direct-jwt"}
    assert seen[0][0] == "https://direct-host/exchange"
    assert seen[0][1]["token"] == "proof-jwt"


def test_workload_identity_adapter_env_names_remain_fallback(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Legacy env-name kwargs still resolve when no direct values are given."""
    proof_file = tmp_path / "token"
    proof_file.write_text("env-proof-jwt", encoding="utf-8")
    monkeypatch.setenv("NIUU_WORKLOAD_IDENTITY_TOKEN_FILE", str(proof_file))
    monkeypatch.setenv("NIUU_WORKLOAD_IDENTITY_EXCHANGE_URL", "https://env-host/exchange")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"token": "env-jwt", "expires_in": 300})

    adapter = WorkloadIdentityBearerTokenAuthAdapter(transport=httpx.MockTransport(handler))

    assert adapter.headers() == {"Authorization": "Bearer env-jwt"}
    assert seen[0] == "https://env-host/exchange"


def test_workload_identity_adapter_names_the_missing_proof_token(tmp_path) -> None:
    """A missing projected token must be loud, not an unsigned request.

    Returning no header sent the call out anonymously, so the failure only ever
    surfaced as a 401 in the *callee's* sidecar log — which is how two clusters
    ran their whole Observatory discovery unauthenticated without a single
    warning on the side that was actually broken.
    """
    adapter = WorkloadIdentityBearerTokenAuthAdapter(
        exchange_url="https://volundr.test/api/v1/tokens/workload/exchange",
        token_file=str(tmp_path / "absent" / "token"),
    )

    with pytest.raises(RuntimeError, match="projected"):
        adapter.headers()


def test_workload_identity_adapter_rejects_an_empty_proof_token(tmp_path) -> None:
    proof_file = tmp_path / "token"
    proof_file.write_text("   ", encoding="utf-8")
    adapter = WorkloadIdentityBearerTokenAuthAdapter(
        exchange_url="https://volundr.test/api/v1/tokens/workload/exchange",
        token_file=str(proof_file),
    )

    with pytest.raises(RuntimeError, match="empty"):
        adapter.headers()


def test_workload_identity_adapter_requires_an_exchange_url(tmp_path) -> None:
    proof_file = tmp_path / "token"
    proof_file.write_text("proof-jwt", encoding="utf-8")
    adapter = WorkloadIdentityBearerTokenAuthAdapter(token_file=str(proof_file))

    with pytest.raises(RuntimeError, match="exchange URL"):
        adapter.headers()


def test_workload_identity_adapter_stays_quiet_when_nothing_was_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller that never asked for workload identity is not misconfigured.

    Ravn outside Kubernetes constructs this adapter by default; there is no
    projected token there and none was ever requested, so it must degrade to
    "no header" rather than fail the call.
    """
    monkeypatch.delenv("NIUU_WORKLOAD_IDENTITY_TOKEN_FILE", raising=False)
    monkeypatch.setattr(
        "niuu.adapters.outbound.http_auth._DEFAULT_SERVICE_ACCOUNT_TOKEN_FILE",
        "/nonexistent/serviceaccount/token",
    )

    adapter = WorkloadIdentityBearerTokenAuthAdapter(base_url="https://volundr.test")

    assert adapter.headers() == {}
