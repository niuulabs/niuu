"""How Forge reaches a session's broker: one set of URL rules for every caller."""

from __future__ import annotations

import pytest

from volundr.adapters.inbound import rest
from volundr.domain.session_broker import (
    DEFAULT_OPENSHELL_INTERNAL_GATEWAY_URL,
    broker_base_url,
    presented_file_url,
    server_side_http_proxy_target,
    session_proxy_url,
)


@pytest.mark.parametrize(
    ("chat_endpoint", "base"),
    [
        ("ws://10.0.0.5:9100/session", "http://10.0.0.5:9100"),
        ("wss://forge.example/s/abc/session", "https://forge.example/s/abc"),
        ("wss://forge.example/s/abc", "https://forge.example/s/abc"),
    ],
)
def test_broker_base_url(chat_endpoint, base):
    assert broker_base_url(chat_endpoint) == base


def test_presented_file_url_is_the_brokers_presented_route():
    assert presented_file_url("http://b:1/s/x", "pf_1") == "http://b:1/s/x/api/files/presented/pf_1"
    assert presented_file_url("http://b:1", "a/b") == "http://b:1/api/files/presented/a%2Fb"


def test_non_openshell_urls_pass_through_and_the_gateway_is_validated():
    assert server_side_http_proxy_target("http://b:1/api") == ("http://b:1/api", {})
    url, headers = server_side_http_proxy_target("http://x--skuld.openshell.localhost:8080/api")
    assert url == f"{DEFAULT_OPENSHELL_INTERNAL_GATEWAY_URL}/api"
    assert headers == {"Host": "x--skuld.openshell.localhost:8080"}
    with pytest.raises(ValueError, match="absolute URL"):
        server_side_http_proxy_target("http://x.openshell.localhost/api", gateway_url="nohost")


def test_session_proxy_url_edges():
    assert session_proxy_url("http://b:1") == "http://b:1/"
    assert session_proxy_url("http://b:1/base/") == "http://b:1/base"
    with pytest.raises(ValueError, match="absolute http"):
        session_proxy_url("ftp://b/x", "api")


def test_rest_proxy_routes_use_the_shared_rules():
    assert rest._session_proxy_url is session_proxy_url
    assert rest._server_side_http_proxy_target is server_side_http_proxy_target
