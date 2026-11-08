"""The authenticated JSON HTTP client the A2A and Forge adapters call peers through."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from niuu.adapters.outbound.http_json_client import (
    HttpxJsonClient,
    client_from_workload_identity,
    normalize_http_origin,
)


def test_client_from_workload_identity_exchanges_projected_token(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("projected-proof", encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://forge.example/api/v1/tokens/workload/exchange"
        assert json.loads(request.content) == {
            "token": "projected-proof",
            "audiences": ["forge"],
        }
        return httpx.Response(200, json={"token": "workload-jwt", "expires_in": 300})

    client = client_from_workload_identity(
        base_url="https://forge.example",
        workload_token_file=str(token_file),
        workload_audiences=["forge"],
        transport=httpx.MockTransport(handler),
    )

    assert client._headers()["Authorization"] == "Bearer workload-jwt"


async def test_http_client_refreshes_rejected_workload_identity_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RefreshableAuth:
        def __init__(self) -> None:
            self.token = "expired"
            self.invalidations = 0

        def headers(self) -> dict[str, str]:
            return {"Authorization": f"Bearer {self.token}"}

        def invalidate(self) -> bool:
            self.invalidations += 1
            self.token = "fresh"
            return True

    class FakeAsyncClient:
        def __init__(self, **_kwargs: object) -> None:
            self.authorizations: list[str] = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, _url: str, *, headers: dict[str, str]) -> httpx.Response:
            authorization = headers["Authorization"]
            self.authorizations.append(authorization)
            status = 401 if authorization == "Bearer expired" else 200
            return httpx.Response(status, json={"ok": status == 200})

    created: list[FakeAsyncClient] = []

    def build_client(**kwargs: object) -> FakeAsyncClient:
        client = FakeAsyncClient(**kwargs)
        created.append(client)
        return client

    monkeypatch.setattr(httpx, "AsyncClient", build_client)
    auth = RefreshableAuth()
    client = HttpxJsonClient(auth=auth, allowed_origins=["https://forge.example"])

    response = await client.get("https://forge.example/api/v1/forge/sessions")

    assert response.status_code == 200
    assert auth.invalidations == 1
    assert created[0].authorizations == ["Bearer expired", "Bearer fresh"]


async def test_http_client_per_call_bearer_bypasses_base_auth_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingBaseAuth:
        def headers(self) -> dict[str, str]:
            raise AssertionError("base workload identity must not be resolved")

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(
            self,
            _url: str,
            *,
            headers: dict[str, str],
            json: dict,
        ) -> httpx.Response:
            assert headers["Authorization"] == "Bearer owner-operation-token"
            assert json == {"jsonrpc": "2.0"}
            return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: FakeAsyncClient())
    client = HttpxJsonClient(
        auth=FailingBaseAuth(),  # type: ignore[arg-type]
        allowed_origins=["http://ravn-child:8080"],
    )

    response = await client.post(
        "http://ravn-child:8080/a2a",
        {"jsonrpc": "2.0"},
        headers={"Authorization": "Bearer owner-operation-token"},
    )

    assert response.status_code == 200


def test_client_external_token_env_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXTERNAL_TOOL_BUILD_TOKEN", "external-123")

    client = client_from_workload_identity(
        base_url="https://forge.example",
        external_token_env="EXTERNAL_TOOL_BUILD_TOKEN",
    )

    assert client._headers()["Authorization"] == "Bearer external-123"


def test_authenticated_http_client_is_bound_to_configured_origins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EXTERNAL_TOOL_BUILD_TOKEN", "external-123")
    client = client_from_workload_identity(
        base_url="https://forge.example/api",
        external_token_env="EXTERNAL_TOOL_BUILD_TOKEN",
        allowed_origins=["https://forge.example", "https://peer.example:443/a2a"],
    )

    client._assert_allowed_origin("https://forge.example/launch")
    client._assert_allowed_origin("https://peer.example/task")
    with pytest.raises(ValueError, match="untrusted origin"):
        client._assert_allowed_origin("https://attacker.example/collect")
    with pytest.raises(ValueError, match="absolute http"):
        client._assert_allowed_origin("file:///tmp/token")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://[2001:db8::1]/a2a", "https://[2001:db8::1]"),
        ("https://[2001:db8::1]:8443/a2a", "https://[2001:db8::1]:8443"),
    ],
)
def test_http_origin_normalization_preserves_ipv6_authority(url: str, expected: str) -> None:
    assert normalize_http_origin(url) == expected
