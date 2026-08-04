"""A flock persona must authenticate Codex the same way Skuld does.

Skuld's broker injects a Codex auth provider whenever the transport class
accepts one. A flock persona runs the same CodexWebSocketTransport but builds
it through Ravn's CLI executor, and that path supplied no provider — so the
Codex CLI opened its websocket with no credential and every persona turn died:

    codex_api::endpoint::responses_websocket: failed to connect to websocket:
    HTTP error: 401 Unauthorized
    drive_loop ERROR: task event_research_frame_… failed: Reconnecting... 2/5

These tests hold both sides to one contract.
"""

from __future__ import annotations

from ravn.cli.runtime_builders import _runtime_cli_transport_kwargs
from ravn.config import Settings

_CODEX = "skuld.transports.codex_ws.CodexWebSocketTransport"


def _settings(**runtime: object) -> Settings:
    return Settings.model_validate(
        {
            "runtime_executor": {"transport_adapter": _CODEX, **runtime},
            "gateway": {
                "platform": {
                    "base_url": "https://volundr.example.test",
                    "workload_token_file": "/var/run/secrets/niuu-workload/token",
                    "workload_exchange_url": (
                        "https://volundr.example.test/api/v1/tokens/workload/exchange"
                    ),
                }
            },
        }
    )


def test_codex_transport_receives_the_configured_auth_provider() -> None:
    settings = _settings(codex_auth_adapter="skuld.codex_auth.VolundrCodexAuthProvider")

    kwargs = _runtime_cli_transport_kwargs(_CODEX, settings)

    provider = kwargs.get("codex_auth_provider")
    assert provider is not None
    assert type(provider).__name__ == "VolundrCodexAuthProvider"


def test_no_adapter_configured_leaves_the_transport_alone() -> None:
    """A host Codex login needs no provider; that is the historical default."""
    kwargs = _runtime_cli_transport_kwargs(_CODEX, _settings())

    assert "codex_auth_provider" not in kwargs


def test_missing_platform_base_url_does_not_build_a_broken_provider() -> None:
    """Without a broker URL the provider could only fail at first use, and it
    would fail as an opaque 401 rather than as configuration."""
    settings = Settings.model_validate(
        {
            "runtime_executor": {
                "transport_adapter": _CODEX,
                "codex_auth_adapter": "skuld.codex_auth.VolundrCodexAuthProvider",
            }
        }
    )

    kwargs = _runtime_cli_transport_kwargs(_CODEX, settings)

    assert "codex_auth_provider" not in kwargs


def test_non_codex_transports_are_untouched() -> None:
    settings = _settings(codex_auth_adapter="skuld.codex_auth.VolundrCodexAuthProvider")

    assert (
        _runtime_cli_transport_kwargs("skuld.transports.subprocess.SubprocessTransport", settings)
        == {}
    )


def test_unconfigured_workload_settings_defer_to_the_runtime_env() -> None:
    """Ravn's workload_token_file defaults to the generic kubernetes.io
    service-account token, whose audience the exchange rejects. A flock
    persona is handed the right path in NIUU_WORKLOAD_IDENTITY_TOKEN_FILE and
    the adapter falls back to it — but only if we do not overwrite it with our
    own default, which produced a credential that could only ever 401."""
    settings = Settings.model_validate(
        {
            "runtime_executor": {
                "transport_adapter": _CODEX,
                "codex_auth_adapter": "skuld.codex_auth.VolundrCodexAuthProvider",
            },
            # base_url only — exactly what a flock persona has once the
            # exchange-URL fallback resolves it.
            "gateway": {"platform": {"base_url": "https://volundr.example.test"}},
        }
    )

    provider = _runtime_cli_transport_kwargs(_CODEX, settings)["codex_auth_provider"]
    auth = provider._http_client_provider.__closure__[0].cell_contents

    assert auth._token_file == ""
    assert auth._token_file_env == "NIUU_WORKLOAD_IDENTITY_TOKEN_FILE"


def test_explicit_workload_settings_are_still_honoured() -> None:
    settings = Settings.model_validate(
        {
            "runtime_executor": {
                "transport_adapter": _CODEX,
                "codex_auth_adapter": "skuld.codex_auth.VolundrCodexAuthProvider",
            },
            "gateway": {
                "platform": {
                    "base_url": "https://volundr.example.test",
                    "workload_token_file": "/custom/token",
                }
            },
        }
    )

    provider = _runtime_cli_transport_kwargs(_CODEX, settings)["codex_auth_provider"]
    auth = provider._http_client_provider.__closure__[0].cell_contents

    assert auth._token_file == "/custom/token"


async def test_client_factory_actually_builds_a_client(monkeypatch) -> None:
    """The earlier tests constructed the provider but never invoked its client
    factory, so `await auth.headers()` — headers() is synchronous and returns a
    dict — got all the way to a live flock and failed there with
    "TypeError: 'dict' object can't be awaited". Call it here."""
    import httpx

    from niuu.adapters.outbound import http_auth

    monkeypatch.setattr(
        http_auth.WorkloadIdentityBearerTokenAuthAdapter,
        "headers",
        lambda self: {"Authorization": "Bearer test-token"},
    )
    settings = _settings(codex_auth_adapter="skuld.codex_auth.VolundrCodexAuthProvider")

    provider = _runtime_cli_transport_kwargs(_CODEX, settings)["codex_auth_provider"]
    client = await provider._http_client_provider()

    assert isinstance(client, httpx.AsyncClient)
    assert client.headers["Authorization"] == "Bearer test-token"
    await client.aclose()
