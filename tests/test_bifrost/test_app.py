"""Tests for the Bifröst FastAPI application."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from bifrost.app import create_app
from bifrost.config import BifrostConfig, ProviderConfig
from bifrost.translation.models import (
    AnthropicResponse,
    TextBlock,
    UsageInfo,
)


def _make_response(text: str = "Hello!") -> AnthropicResponse:
    return AnthropicResponse(
        id="msg_test",
        content=[TextBlock(text=text)],
        model="gpt-4o",
        stop_reason="end_turn",
        usage=UsageInfo(input_tokens=5, output_tokens=3),
    )


def _make_config() -> BifrostConfig:
    return BifrostConfig(
        providers={"openai": ProviderConfig(models=["gpt-4o"])},
        aliases={"smart": "gpt-4o"},
    )


class TestHealthEndpoint:
    def test_health_ok(self):
        app = create_app(_make_config())
        with TestClient(app) as client:
            resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestMessagesEndpoint:
    def _client(self) -> TestClient:
        return TestClient(create_app(_make_config()))

    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_complete_success(self, mock_complete):
        mock_complete.return_value = _make_response("World!")
        with self._client() as client:
            resp = client.post(
                "/api/v1/bifrost/v1/messages",
                json={
                    "model": "gpt-4o",
                    "max_tokens": 100,
                    "messages": [{"role": "user", "content": "Hi"}],
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["content"][0]["text"] == "World!"
        assert body["stop_reason"] == "end_turn"

    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_alias_expansion_in_request(self, mock_complete):
        mock_complete.return_value = _make_response()
        with self._client() as client:
            resp = client.post(
                "/api/v1/bifrost/v1/messages",
                json={
                    "model": "smart",  # alias → gpt-4o
                    "max_tokens": 100,
                    "messages": [{"role": "user", "content": "Hi"}],
                },
            )
        assert resp.status_code == 200

    def test_invalid_request_body_returns_422(self):
        with self._client() as client:
            resp = client.post(
                "/api/v1/bifrost/v1/messages",
                json={"not_a_valid_field": True},
            )
        assert resp.status_code == 422

    def test_malformed_json_returns_422(self):
        with self._client() as client:
            resp = client.post(
                "/api/v1/bifrost/v1/messages",
                content=b"not json",
                headers={"content-type": "application/json"},
            )
        assert resp.status_code == 422

    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_router_error_returns_502(self, mock_complete):
        from bifrost.router import RouterError

        mock_complete.side_effect = RouterError("no provider for model")
        with self._client() as client:
            resp = client.post(
                "/api/v1/bifrost/v1/messages",
                json={
                    "model": "gpt-4o",
                    "max_tokens": 100,
                    "messages": [{"role": "user", "content": "Hi"}],
                },
            )
        assert resp.status_code == 502

    def test_unknown_model_returns_502(self):
        with self._client() as client:
            resp = client.post(
                "/api/v1/bifrost/v1/messages",
                json={
                    "model": "no-such-model-xyz",
                    "max_tokens": 100,
                    "messages": [{"role": "user", "content": "Hi"}],
                },
            )
        assert resp.status_code == 502

    @patch("bifrost.router.ModelRouter.stream")
    def test_streaming_response_returns_event_stream(self, mock_stream):
        async def fake_stream(request):
            yield "event: message_start\ndata: {}\n\n"
            yield "data: [DONE]\n\n"

        mock_stream.return_value = fake_stream(None)

        with self._client() as client:
            resp = client.post(
                "/api/v1/bifrost/v1/messages",
                json={
                    "model": "gpt-4o",
                    "max_tokens": 100,
                    "messages": [{"role": "user", "content": "Hi"}],
                    "stream": True,
                },
            )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]

    @patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock)
    def test_response_includes_usage(self, mock_complete):
        mock_complete.return_value = _make_response()
        with self._client() as client:
            resp = client.post(
                "/api/v1/bifrost/v1/messages",
                json={
                    "model": "gpt-4o",
                    "max_tokens": 100,
                    "messages": [{"role": "user", "content": "Hi"}],
                },
            )
        body = resp.json()
        assert "usage" in body
        assert body["usage"]["input_tokens"] == 5
        assert body["usage"]["output_tokens"] == 3


class TestBuildPatRevocationValidator:
    """bifrost.app._build_pat_revocation_validator — startup-time guards."""

    def _config(self, **pat_revocation) -> BifrostConfig:
        return BifrostConfig(
            providers={"openai": ProviderConfig(models=["gpt-4o"])},
            auth_mode="pat",
            pat_secret="test-secret-key-that-is-at-least-32-bytes-long!",
            pat_revocation=pat_revocation,
        )

    def test_wrong_type_raises_type_error(self):
        import pytest

        from bifrost.app import _build_pat_revocation_validator

        # Any importable class that isn't a PATValidator but tolerates
        # **kwargs (repo=/cache_ttl=/revoked_cache_ttl=) at construction.
        cfg = self._config(adapter="identity.adapters.identity.AllowAllHeaderAuthenticationAdapter")
        with pytest.raises(TypeError, match="PATValidator"):
            _build_pat_revocation_validator(cfg)

    def test_repo_dependent_validator_rejected_at_startup(self):
        """The plain PATValidator base class does not override is_valid(), so

        it would dereference self._repo — but this composition root always
        passes repo=None (Bifröst has no database pool). Catch this at
        startup, not on the first PAT-checked request.
        """
        import pytest

        from bifrost.app import _build_pat_revocation_validator

        cfg = self._config(adapter="niuu.domain.services.pat_validator.PATValidator")
        with pytest.raises(ValueError, match="self._repo"):
            _build_pat_revocation_validator(cfg)

    def test_remote_pat_validator_is_accepted(self):
        """RemotePATValidator overrides is_valid() and never reads repo."""
        from bifrost.app import _build_pat_revocation_validator
        from niuu.adapters.remote_pats import RemotePATValidator

        cfg = self._config(
            adapter="niuu.adapters.remote_pats.RemotePATValidator",
            kwargs={"authority_url": "https://identity.test"},
        )
        validator = _build_pat_revocation_validator(cfg)
        assert isinstance(validator, RemotePATValidator)

    def test_disabled_returns_none_even_with_adapter_configured(self):
        from bifrost.app import _build_pat_revocation_validator

        cfg = self._config(enabled=False, adapter="niuu.adapters.remote_pats.RemotePATValidator")
        assert _build_pat_revocation_validator(cfg) is None
