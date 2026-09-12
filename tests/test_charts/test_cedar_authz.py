"""Render and exercise the Forge Cedar/Envoy deployment contract."""

import subprocess
from pathlib import Path

import pytest
import yaml

from identity.authz_config import AuthorizationGatewayConfig

CHART = Path(__file__).parents[2] / "charts" / "volundr"


def render_cedar(chart=CHART, **overrides):
    values = {
        "pat.token_issuer_adapter": "niuu.adapters.keycloak_token_issuer.KeycloakTokenIssuer",
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
    command = [
        "helm",
        "template",
        "test",
        str(chart),
        "-f",
        str(chart / ("values-auth.yaml" if chart.name == "skuld" else "values-cedar.yaml")),
    ]
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


@pytest.mark.parametrize("chart_name", ["volundr", "niuu-shared", "ting", "skuld"])
def test_explicit_no_auth_profile(chart_name):
    chart = CHART.parent / chart_name
    docs = list(
        yaml.safe_load_all(
            subprocess.check_output(
                [
                    "helm",
                    "template",
                    "test",
                    str(chart),
                    "-f",
                    str(
                        chart
                        / ("values-auth.yaml" if chart_name == "skuld" else "values-cedar.yaml")
                    ),
                    "-f",
                    str(chart / "values-no-auth.yaml"),
                ]
            )
        )
    )
    config = next(
        yaml.safe_load(d["data"]["config.yaml"])
        for d in docs
        if d and d["kind"] == "ConfigMap" and "config.yaml" in d.get("data", {})
    )
    if chart_name == "skuld":
        assert config["ws_auth"]["enforce_ownership"] is False
        return
    assert (
        config["authorization"]["adapter"]
        == "identity.adapters.authorization.AllowAllAuthorizationAdapter"
    )
    if chart_name == "ting":
        assert config["auth"]["allow_anonymous_dev"] is True
    else:
        assert config["identity"]["adapter"] == "identity.adapters.identity.AllowAllIdentityAdapter"


@pytest.mark.parametrize(
    "path,allowed",
    [
        ("/api/v1/ting/workflows/abc/launch", True),
        ("/api/v1/ting/workflows/abc/delete", False),
        ("/api/v1/ting/workflows/abc/nested/launch", False),
        ("/api/v1/ting/workflows//launch", False),
    ],
)
def test_ting_scope_is_bound_to_launch_route(path, allowed):
    docs = render_cedar(chart=CHART.parent / "ting")
    config = next(
        yaml.safe_load(d["data"]["authz.yaml"])
        for d in docs
        if d["kind"] == "ConfigMap" and "authz.yaml" in d["data"]
    )
    route = AuthorizationGatewayConfig.model_validate(config).routes[0]
    assert route.required_scope == "ting:workflow:launch"
    assert route.matches(path) is allowed
    deployment = next(d for d in docs if d["kind"] == "Deployment")
    app = next(
        c for c in deployment["spec"]["template"]["spec"]["containers"] if c["name"] == "ting"
    )
    assert next(e["value"] for e in app["env"] if e["name"] == "HOST") == "127.0.0.1"
    assert app["readinessProbe"]["httpGet"]["port"] == 8443


@pytest.mark.parametrize("path,prefix", [("/api/{id}/launch", True), ("/api/item-{id}", False)])
def test_ambiguous_route_templates_rejected(path, prefix):
    from identity.authz_config import GatewayRoute

    with pytest.raises(ValueError):
        GatewayRoute(path=path, path_template=True, prefix=prefix, methods=["POST"])


def test_ravn_explicit_no_auth_profile():
    chart = CHART.parent / "ravn"
    docs = list(
        yaml.safe_load_all(
            subprocess.check_output(
                ["helm", "template", "test", str(chart), "-f", str(chart / "values-no-auth.yaml")]
            )
        )
    )
    config = next(
        yaml.safe_load(d["data"]["config.yaml"])
        for d in docs
        if d and d["kind"] == "ConfigMap" and "config.yaml" in d.get("data", {})
    )
    assert config["api_auth"]["adapter"].endswith("AllowAllHeaderAuthenticationAdapter")


def test_skuld_secure_profile_has_private_backends_and_verified_jwks():
    chart = CHART.parent / "skuld"
    command = ["helm", "template", "test", str(chart), "-f", str(chart / "values-auth.yaml")]
    for setting in [
        "session.id=one",
        "session.ownerId=alice",
        "session.tenantId=acme",
        "envoy.jwt.issuer=https://issuer.test",
        "envoy.jwt.audiences[0]=skuld",
        "envoy.jwt.jwksUri=https://issuer.test/jwks",
        "envoy.jwt.keycloakHost=issuer.test",
        "envoy.jwt.keycloakTls=true",
        "envoy.jwt.keycloakPort=443",
    ]:
        command.extend(["--set", setting])
    docs = [d for d in yaml.safe_load_all(subprocess.check_output(command)) if d]
    config = next(
        yaml.safe_load(d["data"]["config.yaml"])
        for d in docs
        if d["kind"] == "ConfigMap" and "config.yaml" in d.get("data", {})
    )
    assert config["host"] == "127.0.0.1"
    assert config["ws_auth"]["enforce_ownership"] is True
    assert (
        config["ws_auth"]["authorization"]["adapter"]
        == "identity.adapters.cedar.CedarAuthorizationAdapter"
    )
    nginx = next(
        d["data"]["nginx.conf"]
        for d in docs
        if d["kind"] == "ConfigMap" and "nginx.conf" in d.get("data", {})
    )
    assert "listen 127.0.0.1:8080;" in nginx
    assert "$request_uri" not in nginx.split("log_format", 1)[1].split(";", 1)[0]
    envoy = envoy_config(docs)
    filters = envoy["static_resources"]["listeners"][0]["filter_chains"][0]["filters"][0][
        "typed_config"
    ]["http_filters"]
    assert [f["name"] for f in filters] == [
        "envoy.filters.http.lua",
        "envoy.filters.http.jwt_authn",
        "envoy.filters.http.ext_authz",
        "envoy.filters.http.router",
    ]
    binding = AuthorizationGatewayConfig.model_validate(
        next(
            yaml.safe_load(d["data"]["authz.yaml"])
            for d in docs
            if d["kind"] == "ConfigMap" and "authz.yaml" in d.get("data", {})
        )
    )
    pod = next(d for d in docs if d["kind"] == "Deployment")["spec"]["template"]["spec"]
    assert any(c["name"] == "cedar-authz" for c in pod["containers"])
    policy = next(d for d in docs if d["kind"] == "NetworkPolicy")
    assert policy["spec"]["policyTypes"] == ["Ingress"]
    assert policy["spec"]["ingress"] == [{"ports": [{"protocol": "TCP", "port": 8444}]}]
    assert all("image" not in v and "command" not in v for v in pod["volumes"])
    authz_container = next(c for c in pod["containers"] if c["name"] == "cedar-authz")
    assert "fsGroup" not in authz_container["securityContext"]
    assert authz_container["securityContext"]["readOnlyRootFilesystem"] is True
    assert binding.session.owner_id == "alice"
    assert binding.session.tenant_id == "acme"
    cluster = next(c for c in envoy["static_resources"]["clusters"] if "transport_socket" in c)
    validation = cluster["transport_socket"]["typed_config"]["common_tls_context"][
        "validation_context"
    ]
    assert validation["trusted_ca"]["filename"]
    assert validation["match_typed_subject_alt_names"]


@pytest.mark.parametrize("chart_name", ["volundr", "ting", "skuld"])
async def test_central_identity_wires_edge_application_and_pat_revocation(chart_name):
    chart = CHART.parent / chart_name
    overrides = {
        "identityAuthority.enabled": "true",
        "identityAuthority.url": "https://identity.test",
    }
    if chart_name == "skuld":
        overrides.update(
            {"session.ownerId": "alice", "session.tenantId": "acme", "session.id": "session-test"}
        )
    docs = render_cedar(chart, **overrides)
    configs = [
        yaml.safe_load(d["data"]["config.yaml"])
        for d in docs
        if d["kind"] == "ConfigMap" and "config.yaml" in d.get("data", {})
    ]
    config = configs[0]
    edge = next(
        yaml.safe_load(d["data"]["authz.yaml"])
        for d in docs
        if d["kind"] == "ConfigMap" and "authz.yaml" in d.get("data", {})
    )
    assert edge["identity"]["kwargs"]["authority_url"] == "https://identity.test"
    AuthorizationGatewayConfig.model_validate(edge)
    if chart_name == "skuld":
        assert config["ws_auth"]["identity"] == edge["identity"]
        return
    auth = config["identity" if chart_name == "volundr" else "auth"]
    assert auth["kwargs"]["authority_url"] == "https://identity.test"
    assert config["pat"]["validator_adapter"] == "niuu.adapters.remote_pats.RemotePATValidator"
    assert config["pat"]["service_adapter"] == "niuu.adapters.remote_pats.RemotePATService"


@pytest.mark.parametrize("chart_name", ["volundr", "ting", "skuld", "guild", "observatory"])
def test_no_auth_overrides_central_identity_profile(chart_name):
    chart = CHART.parent / chart_name
    command = [
        "helm",
        "template",
        "test",
        str(chart),
        "-f",
        str(chart / "values-central-identity.yaml"),
        "-f",
        str(chart / "values-no-auth.yaml"),
    ]
    docs = [d for d in yaml.safe_load_all(subprocess.check_output(command)) if d]
    config = next(
        yaml.safe_load(d["data"]["config.yaml"])
        for d in docs
        if d["kind"] == "ConfigMap" and "config.yaml" in d.get("data", {})
    )
    if chart_name == "skuld":
        assert not config["ws_auth"]["enforce_ownership"]
        assert "identity" not in config["ws_auth"]
    elif chart_name == "ting":
        assert config["auth"]["allow_anonymous_dev"]
    else:
        assert config["identity"]["adapter"] == "identity.adapters.identity.AllowAllIdentityAdapter"


@pytest.mark.parametrize("chart_name", ["guild", "observatory"])
def test_standalone_gateway_central_identity_configuration(chart_name):
    chart = CHART.parent / chart_name
    docs = [
        d
        for d in yaml.safe_load_all(
            subprocess.check_output(
                [
                    "helm",
                    "template",
                    "test",
                    str(chart),
                    "-f",
                    str(chart / "values-central-identity.yaml"),
                    "--set",
                    "identityAuthority.url=https://identity.test",
                ]
            )
        )
        if d
    ]
    config = next(
        yaml.safe_load(d["data"]["config.yaml"])
        for d in docs
        if d["kind"] == "ConfigMap" and "config.yaml" in d.get("data", {})
    )
    assert (
        config["identity"]["adapter"]
        == "identity.adapters.remote.RemoteHeaderAuthenticationAdapter"
    )
    assert config["authorization"]["adapter"] == "identity.adapters.cedar.CedarAuthorizationAdapter"
    assert config["pat"]["validator_kwargs"]["authority_url"] == "https://identity.test"
