"""Tests for shared browser-facing session endpoint normalization."""

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
