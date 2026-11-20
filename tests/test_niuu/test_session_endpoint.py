"""Tests for shared browser-facing session endpoint normalization."""

import pytest

from niuu.domain.session_endpoint import public_session_endpoint


def test_public_session_endpoint_preserves_empty_and_external_endpoints() -> None:
    assert public_session_endpoint(None) is None
    assert public_session_endpoint("") == ""
    assert (
        public_session_endpoint("wss://session.example.test/chat")
        == "wss://session.example.test/chat"
    )


def test_public_session_endpoint_maps_openshell_host_to_session_proxy() -> None:
    endpoint = "ws://sandbox-123.openshell.localhost:8080/session"

    assert (
        public_session_endpoint(endpoint, session_id="session/with spaces")
        == "/s/session%2Fwith%20spaces/session"
    )


def test_public_session_endpoint_rewrites_loopback_for_browser_host() -> None:
    endpoint = "ws://127.0.0.1:9000/chat?token=abc#stream"

    assert (
        public_session_endpoint(endpoint, public_host="niuu.example.test")
        == "ws://niuu.example.test:9000/chat?token=abc#stream"
    )
    assert public_session_endpoint(endpoint) == "ws://localhost:9000/chat?token=abc#stream"


@pytest.mark.parametrize(
    "host",
    ["localhost", "0.0.0.0", "[::]", "[::1]", "127.0.0.1", "127.0.1.1", "LOCALHOST"],
)
def test_every_loopback_or_bind_all_form_gets_the_public_host(host: str) -> None:
    """LEX-1847: start_session advertised ws://localhost:8080/... from a 0.0.0.0 bind."""
    endpoint = f"ws://{host}:8080/s/abc/session"

    assert (
        public_session_endpoint(endpoint, session_id="abc", public_host="100.66.123.128")
        == "ws://100.66.123.128:8080/s/abc/session"
    )


def test_a_loopback_public_host_still_reads_as_localhost() -> None:
    assert (
        public_session_endpoint("ws://0.0.0.0:8080/s/abc/session", public_host="127.0.0.1")
        == "ws://localhost:8080/s/abc/session"
    )


@pytest.mark.parametrize("host", ["10.0.0.5", "thor.tail737f2a.ts.net", "localhost.example"])
def test_a_reachable_host_is_left_alone(host: str) -> None:
    endpoint = f"ws://{host}:8080/s/abc/session"
    assert public_session_endpoint(endpoint, public_host="100.66.123.128") == endpoint


def test_an_ipv6_public_host_is_bracketed() -> None:
    assert (
        public_session_endpoint("ws://0.0.0.0:8080/s/abc/session", public_host="fd7a:115c::5")
        == "ws://[fd7a:115c::5]:8080/s/abc/session"
    )
