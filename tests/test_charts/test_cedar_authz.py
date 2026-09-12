"""Render and exercise the Forge Cedar/Envoy deployment contract."""

import subprocess
from pathlib import Path

import pytest
import yaml

from identity.authz_config import AuthorizationGatewayConfig

CHART = Path(__file__).parents[2] / "charts" / "volundr"


def render_cedar(**overrides):
    values = {
        "envoy.enabled": "true",
        "envoy.jwt.enabled": "true",
        "envoy.jwt.issuer": "https://issuer.test",
        "envoy.jwt.audiences[0]": "forge",
        "envoy.jwt.jwksUri": "https://issuer.test/jwks",
        "envoy.jwt.keycloakHost": "issuer.test",
        "envoy.jwt.keycloakTls": "true",
        "envoy.jwt.keycloakPort": "443",
        "envoy.authorization.enabled": "true",
        "authorization.adapter": "identity.adapters.cedar.CedarAuthorizationAdapter",
        "identity.adapter": "volundr.adapters.outbound.identity.EnvoyHeaderIdentityAdapter",
        "networkPolicy.enabled": "true",
        **overrides,
    }
    command = ["helm", "template", "test", str(CHART), "-f", str(CHART / "values-cedar.yaml")]
    for key, value in values.items():
        command.extend(["--set", f"{key}={value}"])
    return [d for d in yaml.safe_load_all(subprocess.check_output(command)) if d]


def envoy_config(documents):
    return next(
        yaml.safe_load(d["data"]["envoy.yaml"])
        for d in documents
        if d["kind"] == "ConfigMap" and "envoy.yaml" in d["data"]
    )


def test_gateway_filter_order_and_fail_closed():
    config = envoy_config(render_cedar())
    http = config["static_resources"]["listeners"][0]["filter_chains"][0]["filters"][0][
        "typed_config"
    ]
    assert [f["name"] for f in http["http_filters"]] == [
        "envoy.filters.http.lua",
        "envoy.filters.http.jwt_authn",
        "envoy.filters.http.ext_authz",
        "envoy.filters.http.router",
    ]
    authz = http["http_filters"][-2]["typed_config"]
    assert authz["failure_mode_allow"] is False
    assert authz["metadata_context_namespaces"] == ["envoy.filters.http.jwt_authn"]
    public_routes = [
        r
        for r in http["route_config"]["virtual_hosts"][0]["routes"]
        if "typed_per_filter_config" in r
    ]
    assert all("path" in r["match"] or r["match"].get("headers") for r in public_routes)


def test_private_listeners_and_network_policy():
    documents = render_cedar()
    deployment = next(
        d
        for d in documents
        if d["kind"] == "Deployment"
        and any(c["name"] == "cedar-authz" for c in d["spec"]["template"]["spec"]["containers"])
    )
    containers = deployment["spec"]["template"]["spec"]["containers"]
    app = next(c for c in containers if c["name"] == "volundr")
    assert next(e["value"] for e in app["env"] if e["name"] == "HOST") == "127.0.0.1"
    assert app["readinessProbe"]["httpGet"]["port"] == 8443
    policy = next(d for d in documents if d["kind"] == "NetworkPolicy")
    assert policy["spec"]["ingress"][0]["ports"] == [{"protocol": "TCP", "port": 8443}]
    assert "checksum/authz" in deployment["spec"]["template"]["metadata"]["annotations"]
    cfg = next(
        yaml.safe_load(d["data"]["authz.yaml"])
        for d in documents
        if d["kind"] == "ConfigMap" and "authz.yaml" in d["data"]
    )
    assert AuthorizationGatewayConfig.model_validate(cfg).port == 9002
    cluster = next(
        c
        for c in envoy_config(documents)["static_resources"]["clusters"]
        if c["name"] == "keycloak"
    )
    tls = cluster["transport_socket"]["typed_config"]
    assert tls["common_tls_context"]["validation_context"]["match_typed_subject_alt_names"] == [
        {"san_type": "DNS", "matcher": {"exact": "issuer.test"}},
    ]
    assert tls["common_tls_context"]["validation_context"]["trusted_ca"]["filename"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"envoy.jwt.enabled": "false"},
        {"envoy.enabled": "false"},
        {"envoy.jwt.keycloakTls": "false"},
        {
            "authorization.adapter": (
                "volundr.adapters.outbound.authorization.AllowAllAuthorizationAdapter"
            )
        },
        {"identity.adapter": "volundr.adapters.outbound.identity.AllowAllIdentityAdapter"},
    ],
)
def test_unsafe_gateway_configuration_rejected(overrides):
    with pytest.raises(subprocess.CalledProcessError):
        render_cedar(**overrides)


def test_hardened_pat_revocation_settings_reach_application():
    documents = render_cedar()
    config = next(
        yaml.safe_load(d["data"]["config.yaml"])
        for d in documents
        if d["kind"] == "ConfigMap" and "config.yaml" in d["data"]
    )
    assert config["pat"]["revocation_cache_ttl"] == 0
    assert config["pat"]["websocket_check_interval"] == 5
