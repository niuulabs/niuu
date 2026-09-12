"""Render and exercise the Forge Cedar/Envoy deployment contract."""

import subprocess
from pathlib import Path

import pytest
import yaml

from identity.authz_config import AuthorizationGatewayConfig

CHART = Path(__file__).parents[2] / "charts" / "volundr"


def render_cedar(chart=CHART, **overrides):
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
    command = ["helm", "template", "test", str(chart), "-f", str(chart / "values-cedar.yaml")]
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
    volumes = deployment["spec"]["template"]["spec"]["volumes"]
    assert all("image" not in volume and "command" not in volume for volume in volumes)
    assert next(v for v in volumes if v["name"] == "authz-config")["configMap"]
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


def test_shared_gateway_listener_and_resource_authorization():
    documents = render_cedar(chart=CHART.parent / "niuu-shared")
    deployment = next(d for d in documents if d["kind"] == "Deployment")
    pod = deployment["spec"]["template"]["spec"]
    app = next(c for c in pod["containers"] if c["name"] == "niuu-shared")
    assert app["command"] == ["python", "-m", "uvicorn"]
    assert app["args"] == ["cli.shared_host:app", "--host", "127.0.0.1", "--port", "8082"]
    assert app["readinessProbe"]["httpGet"]["port"] == 8443
    assert any(c["name"] == "cedar-authz" for c in pod["containers"])
    assert all("image" not in v for v in pod["volumes"])
    config = next(
        yaml.safe_load(d["data"]["config.yaml"])
        for d in documents
        if d["kind"] == "ConfigMap" and "config.yaml" in d["data"]
    )
    assert config["authorization"]["adapter"] == "identity.adapters.cedar.CedarAuthorizationAdapter"
    assert config["pat"]["revocation_cache_ttl"] == 0
    gateway = next(
        yaml.safe_load(d["data"]["authz.yaml"])
        for d in documents
        if d["kind"] == "ConfigMap" and "authz.yaml" in d["data"]
    )
    settings = AuthorizationGatewayConfig.model_validate(gateway)
    assert any(r.path == "/api/v1/credentials" for r in settings.routes)
    assert all(r.path != "/api/v1" for r in settings.routes)
    filters = envoy_config(documents)["static_resources"]["listeners"][0]["filter_chains"][0][
        "filters"
    ][0]["typed_config"]["http_filters"]
    assert [f["name"] for f in filters][-2:] == [
        "envoy.filters.http.ext_authz",
        "envoy.filters.http.router",
    ]


@pytest.mark.parametrize("chart_name", ["volundr", "niuu-shared"])
@pytest.mark.parametrize(
    "bad", [{"envoy.jwt.workload.jwksTls": "false"}, {"envoy.jwt.workload.jwksHost": ""}]
)
def test_remote_workload_keys_require_verified_tls(chart_name, bad):
    values = {
        "envoy.jwt.workload.enabled": "true",
        "envoy.jwt.workload.issuer": "https://workload.test",
        "envoy.jwt.workload.audiences[0]": "forge",
        "envoy.jwt.workload.jwksUri": "https://workload.test/jwks",
        "envoy.jwt.workload.jwksHost": "workload.test",
        **bad,
    }
    with pytest.raises(subprocess.CalledProcessError):
        render_cedar(chart=CHART.parent / chart_name, **values)
