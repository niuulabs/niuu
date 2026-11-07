"""Live preview in the Skuld and Volundr charts, rendered with ``helm template``.

Skuld: the pod's preview config, nginx and Envoy routes and the ext_authz route.
Volundr: the ``*.<preview domain>`` listener, route and OIDC policy.
"""

from __future__ import annotations

import copy
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
import yaml
from envoy.service.auth.v3 import external_auth_pb2
from google.protobuf.json_format import ParseDict

from identity.adapters.cedar import CedarAuthorizationAdapter
from identity.adapters.envoy_authz import EnvoyAuthorizationService
from identity.authz_config import AuthorizationGatewayConfig
from skuld.config import SkuldSettings
from skuld.preview import build_preview

CHARTS = Path(__file__).parent.parent.parent / "charts"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required")

OWNER = "11111111-2222-3333-4444-555555555555"
TENANT = "acme"
OPERATOR_ISSUER = "https://auth.test/realms/niuu"
WORKLOAD_ISSUER = "https://forge.niuu.test/api/v1/tokens/workload"


def merged(base: dict, overrides: dict | None) -> dict:
    result = copy.deepcopy(base)
    for key, value in (overrides or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merged(result[key], value)
            continue
        result[key] = value
    return result


def helm_template(chart: str, tmp_path: Path, values: dict) -> subprocess.CompletedProcess:
    values_file = tmp_path / f"{chart}-values.yaml"
    values_file.write_text(yaml.safe_dump(values))
    return subprocess.run(
        ["helm", "template", "rel", str(CHARTS / chart), "-f", str(values_file)],
        capture_output=True,
        text=True,
        check=False,
    )


def render(chart: str, tmp_path: Path, values: dict) -> list[dict]:
    result = helm_template(chart, tmp_path, values)
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def of_kind(docs: list[dict], kind: str) -> list[dict]:
    return [doc for doc in docs if doc["kind"] == kind]


def named(docs: list[dict], kind: str, name: str) -> dict:
    (doc,) = [d for d in of_kind(docs, kind) if d["metadata"]["name"] == name]
    return doc


def preview_listener(gateway: dict) -> dict:
    (listener,) = [i for i in gateway["spec"]["listeners"] if i["name"] == "preview-https"]
    return listener


def configmap_file(docs: list[dict], filename: str) -> str | None:
    for doc in of_kind(docs, "ConfigMap"):
        if filename in doc["data"]:
            return doc["data"][filename]
    return None


def find_all(node: Any, predicate) -> list[dict]:
    found: list[dict] = []
    if isinstance(node, dict):
        if predicate(node):
            found.append(node)
        for value in node.values():
            found += find_all(value, predicate)
    elif isinstance(node, list):
        for item in node:
            found += find_all(item, predicate)
    return found


def jwt_authn(envoy: dict) -> dict:
    (config,) = [
        node["typed_config"]
        for node in find_all(envoy, lambda n: n.get("name") == "envoy.filters.http.jwt_authn")
    ]
    return config


# --------------------------------------------------------------------------- skuld

SKULD_BASE = {
    "session": {"id": "sess-1", "ownerId": OWNER, "tenantId": TENANT},
    "envoy": {
        "enabled": True,
        "jwt": {
            "enabled": True,
            "issuer": OPERATOR_ISSUER,
            "audiences": ["volundr"],
            "jwksUri": f"{OPERATOR_ISSUER}/protocol/openid-connect/certs",
            "keycloakHost": "auth.test",
            "keycloakPort": 443,
            "keycloakTls": True,
            "workload": {
                "enabled": True,
                "issuer": WORKLOAD_ISSUER,
                "audiences": ["volundr-api"],
                "jwksUri": f"{WORKLOAD_ISSUER}/jwks",
                "jwksHost": "forge.niuu.test",
                "jwksTls": True,
            },
        },
    },
}
SKULD_PREVIEW = {"preview": {"enabled": True}}
SKULD_ENFORCED = {
    "wsAuth": {
        "enforce_ownership": True,
        "authorization": {"adapter": "identity.adapters.cedar.CedarAuthorizationAdapter"},
    }
}


def skuld(tmp_path: Path, *overrides: dict) -> list[dict]:
    values = SKULD_BASE
    for extra in overrides:
        values = merged(values, extra)
    return render("skuld", tmp_path, values)


class TestSkuldPreviewDisabled:
    def test_default_values_render_nothing_preview_related(self, tmp_path):
        docs = render("skuld", tmp_path, {})

        text = yaml.safe_dump(docs)
        assert "/preview/" not in text
        assert "workload_preview" not in text
        assert "forge:session:preview" not in text
        assert "preview_adapters" not in text
        config = yaml.safe_load(configmap_file(docs, "config.yaml"))
        assert "preview" not in config
        assert SkuldSettings(**config).preview.enabled is False

    def test_configured_workload_but_preview_off_renders_nothing(self, tmp_path):
        docs = skuld(tmp_path, SKULD_ENFORCED)

        assert "/preview/" not in yaml.safe_dump(docs)
        assert "preview" not in yaml.safe_load(configmap_file(docs, "config.yaml"))

    def test_the_values_default_is_off(self):
        values = yaml.safe_load((CHARTS / "skuld" / "values.yaml").read_text())

        assert values["preview"]["enabled"] is False
        assert values["preview"]["routerAudience"] == "volundr-api"


class TestSkuldPreviewEnabled:
    def test_enabled_without_the_workload_issuer_fails_with_the_remedy(self, tmp_path):
        result = helm_template("skuld", tmp_path, {"preview": {"enabled": True}})

        assert result.returncode != 0
        assert "preview.enabled needs envoy.jwt.workload.issuer" in result.stderr
        assert "envoy.jwt.workload.jwksUri" in result.stderr

    def test_enabled_with_only_an_issuer_still_fails(self, tmp_path):
        values = {"preview": {"enabled": True}, "envoy": {"jwt": {"workload": {"issuer": "i"}}}}

        result = helm_template("skuld", tmp_path, values)

        assert result.returncode != 0
        assert "envoy.jwt.workload.jwksUri" in result.stderr

    def test_the_pod_config_scans_proc_and_verifies_the_workload_credential(self, tmp_path):
        docs = skuld(tmp_path, SKULD_PREVIEW)

        config = yaml.safe_load(configmap_file(docs, "config.yaml"))
        preview = config["preview"]
        assert preview["enabled"] is True
        assert preview["port_scan_interval_seconds"] == 2
        assert preview["port_scanner"]["adapter"] == (
            "skuld.preview_adapters.ProcNetListeningPortScanner"
        )
        assert preview["router_auth"] == {
            "adapter": "skuld.preview_adapters.WorkloadJwtRouterAuth",
            "kwargs": {
                "issuer": WORKLOAD_ISSUER,
                "audience": "volundr-api",
                "jwks_uri": f"{WORKLOAD_ISSUER}/jwks",
            },
        }

    def test_excluded_ports_are_ints_and_cover_the_pods_infrastructure(self, tmp_path):
        docs = skuld(
            tmp_path,
            SKULD_PREVIEW,
            {"preview": {"excludedPorts": [5432, "6000", 5432]}},
        )

        excluded = yaml.safe_load(configmap_file(docs, "config.yaml"))["preview"]["port_scanner"][
            "kwargs"
        ]["excluded_ports"]

        assert all(type(port) is int for port in excluded)
        assert len(excluded) == len(set(excluded))
        assert {5432, 6000} <= set(excluded)
        # nginx/service, broker, Envoy, its admin and authz listeners, terminal, mesh, shim.
        assert {8080, 8081, 8444, 9901, 9000, 7681, 7480, 7481, 7482, 7483} <= set(excluded)
        assert {7580, 7581, 7781, 18790} <= set(excluded)

    def test_changed_chart_ports_follow_into_the_exclusions(self, tmp_path):
        docs = skuld(
            tmp_path,
            SKULD_PREVIEW,
            {
                "service": {"port": 9191},
                "envoy": {"port": 9292, "adminPort": 9393, "authorization": {"port": 9494}},
                "localServices": {"terminal": {"port": 9595}},
            },
        )

        excluded = yaml.safe_load(configmap_file(docs, "config.yaml"))["preview"]["port_scanner"][
            "kwargs"
        ]["excluded_ports"]

        assert {9191, 9292, 9393, 9494, 9595} <= set(excluded)

    def test_the_audience_and_scan_interval_are_values(self, tmp_path):
        docs = skuld(
            tmp_path,
            {"preview": {"enabled": True, "routerAudience": "preview-aud"}},
            {"preview": {"portScanIntervalSeconds": 5}},
        )

        preview = yaml.safe_load(configmap_file(docs, "config.yaml"))["preview"]

        assert preview["router_auth"]["kwargs"]["audience"] == "preview-aud"
        assert preview["port_scan_interval_seconds"] == 5

    def test_the_rendered_config_builds_in_skuld(self, tmp_path):
        docs = skuld(tmp_path, SKULD_PREVIEW)
        config = yaml.safe_load(configmap_file(docs, "config.yaml"))

        settings = SkuldSettings(**config)
        watcher, forwarder = build_preview(
            settings.preview, session_id="sess-1", emit=lambda frame: None
        )

        assert watcher is not None and forwarder is not None

    def test_nginx_streams_preview_requests_unbuffered(self, tmp_path):
        docs = skuld(tmp_path, SKULD_PREVIEW, {"preview": {"streamIdleTimeout": "900s"}})

        nginx = configmap_file(docs, "nginx.conf")

        start = nginx.index("location /preview/ {")
        block = nginx[start : nginx.index("}", start)]
        assert "proxy_pass http://broker;" in block
        assert "proxy_http_version 1.1;" in block
        assert "proxy_buffering off;" in block
        assert "proxy_request_buffering off;" in block
        assert "proxy_read_timeout 900s;" in block
        assert "proxy_send_timeout 900s;" in block
        assert "client_max_body_size 0;" in block
        assert "proxy_set_header Upgrade $http_upgrade;" in block
        assert "proxy_set_header Connection $connection_upgrade;" in block
        assert "map $http_upgrade $connection_upgrade" in nginx

    def test_nginx_has_no_preview_location_when_disabled(self, tmp_path):
        nginx = configmap_file(skuld(tmp_path), "nginx.conf")

        assert "location /preview/" not in nginx

    def test_envoy_routes_preview_without_a_route_timeout(self, tmp_path):
        docs = skuld(tmp_path, SKULD_PREVIEW, {"preview": {"streamIdleTimeout": "900s"}})

        envoy = yaml.safe_load(configmap_file(docs, "envoy.yaml"))

        (route,) = find_all(
            envoy, lambda n: n.get("match") == {"prefix": "/preview/"} and "route" in n
        )
        assert route["route"]["timeout"] == "0s"
        assert route["route"]["idle_timeout"] == "900s"
        assert route["route"]["cluster"] == "local_nginx"

    def test_envoy_has_no_preview_route_when_disabled(self, tmp_path):
        envoy = configmap_file(skuld(tmp_path), "envoy.yaml")

        assert "/preview/" not in envoy

    def test_envoy_reads_the_routers_credential_from_the_header_only(self, tmp_path):
        # The app's query string is its own: Vite's hot-reload socket sends ?token=<its
        # token>, which Envoy would reject as a malformed JWT if it looked there.
        envoy = yaml.safe_load(configmap_file(skuld(tmp_path, SKULD_PREVIEW), "envoy.yaml"))

        jwt = jwt_authn(envoy)
        assert jwt["rules"][0] == {
            "match": {"prefix": "/preview/"},
            "requires": {"provider_name": "workload_preview"},
        }
        preview = jwt["providers"]["workload_preview"]
        assert "from_params" not in preview
        assert preview["from_headers"] == [{"name": "Authorization", "value_prefix": "Bearer "}]
        workload = dict(jwt["providers"]["workload"])
        assert workload.pop("from_params") == ["token"]
        assert preview == workload
        assert jwt["providers"]["keycloak"]["from_params"] == ["token", "access_token"]

    def test_the_other_rules_are_unchanged_behind_the_preview_rule(self, tmp_path):
        with_preview = jwt_authn(
            yaml.safe_load(configmap_file(skuld(tmp_path, SKULD_PREVIEW), "envoy.yaml"))
        )
        without = jwt_authn(yaml.safe_load(configmap_file(skuld(tmp_path), "envoy.yaml")))

        assert with_preview["rules"][1:] == without["rules"]
        assert list(without["providers"]) == ["keycloak", "workload"]

    def test_enabled_jwt_without_the_workload_provider_fails_with_the_remedy(self, tmp_path):
        values = merged(
            merged(SKULD_BASE, SKULD_PREVIEW), {"envoy": {"jwt": {"workload": {"enabled": False}}}}
        )

        result = helm_template("skuld", tmp_path, values)

        assert result.returncode != 0
        assert "preview.enabled with envoy.jwt.enabled needs envoy.jwt.workload.enabled" in (
            result.stderr
        )

    def test_no_envoy_means_no_envoy_route(self, tmp_path):
        docs = render(
            "skuld",
            tmp_path,
            merged(SKULD_BASE, {**SKULD_PREVIEW, "envoy": {"enabled": False}}),
        )

        assert configmap_file(docs, "envoy.yaml") is None
        assert "location /preview/" in configmap_file(docs, "nginx.conf")


class TestSkuldPreviewAuthorization:
    def _authz(self, tmp_path) -> dict:
        docs = skuld(tmp_path, SKULD_PREVIEW, SKULD_ENFORCED)
        return yaml.safe_load(configmap_file(docs, "authz.yaml"))

    def test_the_preview_route_requires_its_scope_and_comes_first(self, tmp_path):
        authz = self._authz(tmp_path)

        assert authz["routes"][0] == {
            "path": "/preview/",
            "prefix": True,
            "methods": ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            "required_scope": "forge:session:preview",
        }
        assert authz["routes"][1]["path"] == "/"
        assert "required_scope" not in authz["routes"][1]
        AuthorizationGatewayConfig.model_validate(authz)

    def test_no_preview_route_when_disabled(self, tmp_path):
        docs = skuld(tmp_path, SKULD_ENFORCED)

        authz = yaml.safe_load(configmap_file(docs, "authz.yaml"))

        assert authz["routes"][0]["path"] == "/"
        assert all(
            route.get("required_scope") != "forge:session:preview" for route in authz["routes"]
        )

    def test_no_authz_config_without_enforcement(self, tmp_path):
        docs = skuld(tmp_path, SKULD_PREVIEW)

        assert configmap_file(docs, "authz.yaml") is None


def _claims(**overrides) -> dict:
    claims = {
        "iss": WORKLOAD_ISSUER,
        "aud": ["volundr-api"],
        "exp": time.time() + 300,
        "sub": OWNER,
        "tenant_id": TENANT,
        "resource_access": {
            "volundr-api": {"roles": ["volundr:developer"]},
            "volundr": {"roles": ["volundr:developer"]},
        },
    }
    return {**claims, **overrides}


def _check_request(claims: dict, path: str, method: str = "GET"):
    return ParseDict(
        {
            "attributes": {
                "request": {"http": {"path": path, "method": method}},
                "metadata_context": {
                    "filter_metadata": {"envoy.filters.http.jwt_authn": {"jwt_payload": claims}}
                },
            }
        },
        external_auth_pb2.CheckRequest(),
    )


class TestSkuldPreviewThroughCedar:
    """The rendered ext_authz config, through the real Cedar engine."""

    @pytest.fixture
    def gateway(self, tmp_path):
        config = AuthorizationGatewayConfig.model_validate(self_authz(tmp_path))
        return EnvoyAuthorizationService(CedarAuthorizationAdapter(), config)

    async def _status(self, gateway, claims, path, method="GET"):
        response = await gateway.Check(_check_request(claims, path, method), None)
        if response.HasField("ok_response"):
            return 200
        return response.denied_response.status.code

    @staticmethod
    def _router(**overrides):
        return _claims(token_use="valkyrie_build", scopes=["forge:session:preview"], **overrides)

    @pytest.mark.parametrize("method", ["GET", "POST", "PUT", "DELETE", "OPTIONS"])
    async def test_the_routers_credential_reaches_preview_paths(self, gateway, method):
        assert await self._status(gateway, self._router(), "/preview/3000/api", method) == 200

    async def test_the_routers_credential_is_admitted_nowhere_else(self, gateway):
        for path in ("/api/conversation/history", "/session", "/health", "/"):
            assert await self._status(gateway, self._router(), path) == 403

    async def test_a_scoped_credential_without_the_scope_is_refused_on_preview_paths(self, gateway):
        claims = _claims(token_use="valkyrie_build", scopes=["forge:session:create"])

        assert await self._status(gateway, claims, "/preview/3000/") == 403

    async def test_the_credential_of_another_owner_is_refused(self, gateway):
        other = self._router(sub="99999999-0000-0000-0000-000000000000")

        assert await self._status(gateway, other, "/preview/3000/") == 403

    async def test_the_owner_with_an_ordinary_token_still_reaches_the_pod(self, gateway):
        assert await self._status(gateway, _claims(), "/api/health") == 200


def self_authz(tmp_path) -> dict:
    docs = skuld(tmp_path, SKULD_PREVIEW, SKULD_ENFORCED)
    return yaml.safe_load(configmap_file(docs, "authz.yaml"))


# --------------------------------------------------------------------------- volundr

VOLUNDR_BASE = {
    "global": {"domain": "niuu.example"},
    "bifrost": {"url": "http://bifrost.test"},
    "workloadIdentity": {"enabled": True},
    "sessionGateway": {"enabled": True},
    "sessionPreview": {
        "enabled": True,
        "oidc": {
            "issuer": "https://auth.niuu.example/realms/niuu",
            "clientId": "niuu-preview",
            "clientSecretName": "preview-oidc",
        },
    },
}


def volundr(tmp_path: Path, *overrides: dict) -> list[dict]:
    values = VOLUNDR_BASE
    for extra in overrides:
        values = merged(values, extra)
    return render("volundr", tmp_path, values)


def volundr_failure(tmp_path: Path, *overrides: dict) -> str:
    values = VOLUNDR_BASE
    for extra in overrides:
        values = merged(values, extra)
    result = helm_template("volundr", tmp_path, values)
    assert result.returncode != 0, "render should have failed"
    return result.stderr


class TestVolundrPreviewDisabled:
    def test_default_renders_no_preview_resources(self, tmp_path):
        docs = render("volundr", tmp_path, {"bifrost": {"url": "http://bifrost.test"}})

        text = yaml.safe_dump(docs)
        assert "session-preview" not in text
        assert "preview-https" not in text
        assert "session_preview" not in text
        assert not [d for d in docs if d["kind"] == "SecurityPolicy"]

    def test_gateway_enabled_but_preview_off_has_no_preview_listener(self, tmp_path):
        docs = volundr(tmp_path, {"sessionPreview": {"enabled": False}})

        (gateway,) = of_kind(docs, "Gateway")
        assert "preview-https" not in [
            listener["name"] for listener in gateway["spec"]["listeners"]
        ]
        assert not [d for d in docs if d["metadata"]["name"].endswith("-preview")]


class TestVolundrPreviewEnabled:
    def test_the_gateway_gets_a_wildcard_listener_on_the_preview_domain(self, tmp_path):
        docs = volundr(tmp_path)

        (gateway,) = of_kind(docs, "Gateway")
        listener = preview_listener(gateway)
        assert listener["hostname"] == "*.preview.niuu.example"
        assert listener["protocol"] == "HTTPS"
        assert listener["port"] == 443
        assert listener["tls"]["mode"] == "Terminate"
        assert listener["tls"]["certificateRefs"] == [
            {"name": f"{gateway['metadata']['name']}-preview-tls"}
        ]
        assert listener["allowedRoutes"] == {"namespaces": {"from": "Same"}}

    def test_domain_and_tls_secret_overrides(self, tmp_path):
        docs = volundr(
            tmp_path,
            {"sessionPreview": {"domain": "p.example.org", "tlsSecretName": "wild-cert"}},
        )

        (gateway,) = of_kind(docs, "Gateway")
        listener = preview_listener(gateway)
        assert listener["hostname"] == "*.p.example.org"
        assert listener["tls"]["certificateRefs"] == [{"name": "wild-cert"}]
        route = named(docs, "HTTPRoute", "rel-volundr-preview")
        assert route["spec"]["hostnames"] == ["*.p.example.org"]

    def test_the_httproute_sends_every_preview_host_to_forge_unbounded(self, tmp_path):
        docs = volundr(tmp_path)

        route = named(docs, "HTTPRoute", "rel-volundr-preview")
        (gateway,) = of_kind(docs, "Gateway")
        assert route["metadata"]["name"] == "rel-volundr-preview"
        assert route["spec"]["hostnames"] == ["*.preview.niuu.example"]
        (parent,) = route["spec"]["parentRefs"]
        assert parent["name"] == gateway["metadata"]["name"]
        assert parent["sectionName"] == "preview-https"
        (rule,) = route["spec"]["rules"]
        (backend,) = rule["backendRefs"]
        assert backend["name"] == "rel-volundr"
        assert backend["port"] == 80  # the chart's service.port
        assert rule["timeouts"] == {"request": "0s"}

    def test_the_security_policy_signs_in_on_the_preview_domain_only(self, tmp_path):
        docs = volundr(tmp_path)

        policy = named(docs, "SecurityPolicy", "rel-volundr-preview")
        (target,) = policy["spec"]["targetRefs"]
        assert target == {
            "group": "gateway.networking.k8s.io",
            "kind": "HTTPRoute",
            "name": "rel-volundr-preview",
        }
        oidc = policy["spec"]["oidc"]
        assert oidc["provider"] == {"issuer": "https://auth.niuu.example/realms/niuu"}
        assert oidc["clientID"] == "niuu-preview"
        assert oidc["clientSecret"] == {"name": "preview-oidc"}
        assert oidc["redirectURL"] == "https://auth.preview.niuu.example/oauth2/callback"
        assert oidc["cookieDomain"] == "preview.niuu.example"
        assert oidc["forwardAccessToken"] is True
        assert oidc["refreshToken"] is True
        assert oidc["logoutPath"] == "/.niuu/preview/logout"
        assert oidc["scopes"] == ["openid", "profile", "email"]

    def test_callback_host_scopes_and_logout_path_are_values(self, tmp_path):
        docs = volundr(
            tmp_path,
            {
                "sessionPreview": {
                    "oidc": {
                        "callbackHost": "login.example.org",
                        "scopes": ["openid"],
                        "logoutPath": "/bye",
                    }
                }
            },
        )

        oidc = of_kind(docs, "SecurityPolicy")[0]["spec"]["oidc"]

        assert oidc["redirectURL"] == "https://login.example.org/oauth2/callback"
        assert oidc["scopes"] == ["openid"]
        assert oidc["logoutPath"] == "/bye"
        assert oidc["cookieDomain"] == "preview.niuu.example"

    def test_the_forge_config_carries_session_preview(self, tmp_path):
        docs = volundr(
            tmp_path,
            {"sessionPreview": {"credentialTtlSeconds": 90, "credentialAudience": "forge-aud"}},
        )

        config = yaml.safe_load(configmap_file(docs, "config.yaml"))

        assert config["session_preview"] == {
            "enabled": True,
            "domain": "preview.niuu.example",
            "credential_ttl_seconds": 90,
            "credential_audience": "forge-aud",
        }

    def test_the_forge_config_validates_as_session_preview(self, tmp_path):
        from niuu.config_models import SessionPreviewConfig

        docs = volundr(tmp_path)
        config = yaml.safe_load(configmap_file(docs, "config.yaml"))

        parsed = SessionPreviewConfig(**config["session_preview"])

        assert parsed.enabled and parsed.domain == "preview.niuu.example"

    def test_forges_envoy_streams_preview_hosts_unbounded_ahead_of_the_catch_all(self, tmp_path):
        docs = volundr(
            tmp_path,
            {"envoy": {"enabled": True}, "sessionPreview": {"streamIdleTimeout": "600s"}},
        )

        envoy = configmap_file(docs, "envoy.yaml")
        assert envoy is not None
        config = yaml.safe_load(envoy)
        hosts = find_all(config, lambda n: "virtual_hosts" in n)[0]["virtual_hosts"]
        names = [host["name"] for host in hosts]
        assert names.index("session-preview") < names.index("backend")
        preview = hosts[names.index("session-preview")]
        assert preview["domains"] == ["*.preview.niuu.example", "*.preview.niuu.example:*"]
        (route,) = preview["routes"]
        assert route["route"]["timeout"] == "0s"
        assert route["route"]["idle_timeout"] == "600s"


class TestVolundrPreviewRequirements:
    def test_without_workload_identity_fails_with_the_remedy(self, tmp_path):
        stderr = volundr_failure(tmp_path, {"workloadIdentity": {"enabled": False}})

        assert "sessionPreview.enabled needs workloadIdentity.enabled" in stderr

    def test_without_the_session_gateway_fails_with_the_remedy(self, tmp_path):
        stderr = volundr_failure(tmp_path, {"sessionGateway": {"enabled": False}})

        assert "sessionPreview.enabled needs sessionGateway.enabled" in stderr

    @pytest.mark.parametrize(
        ("field", "message"),
        [
            ("issuer", "sessionPreview.oidc.issuer is required"),
            ("clientId", "sessionPreview.oidc.clientId is required"),
            ("clientSecretName", "sessionPreview.oidc.clientSecretName is required"),
        ],
    )
    def test_missing_oidc_fields_fail_with_the_remedy(self, tmp_path, field, message):
        stderr = volundr_failure(tmp_path, {"sessionPreview": {"oidc": {field: ""}}})

        assert message in stderr


VOLUNDR_JWT = {
    "envoy": {
        "enabled": True,
        "jwt": {
            "enabled": True,
            "issuer": OPERATOR_ISSUER,
            "audiences": ["volundr-api"],
            "jwksUri": f"{OPERATOR_ISSUER}/protocol/openid-connect/certs",
            "keycloakHost": "auth.test",
            "bypassPrefixes": ["/health"],
            "workload": {
                "enabled": True,
                "issuer": WORKLOAD_ISSUER,
                "audiences": ["volundr-api"],
                "jwksUri": f"{WORKLOAD_ISSUER}/jwks",
                "jwksHost": "forge.niuu.test",
            },
        },
    }
}


def forge_jwt(tmp_path: Path, *overrides: dict) -> dict:
    envoy = configmap_file(volundr(tmp_path, VOLUNDR_JWT, *overrides), "envoy.yaml")
    return jwt_authn(yaml.safe_load(envoy))


class TestVolundrPreviewJwt:
    """Preview hosts read the caller's JWT from Authorization, never the app's query."""

    @staticmethod
    def _preview_rule(jwt: dict) -> tuple[int, dict]:
        (found,) = [
            (index, rule)
            for index, rule in enumerate(jwt["rules"])
            if any(h["name"] == ":authority" for h in rule["match"].get("headers", []))
        ]
        return found

    def test_preview_hosts_use_header_only_providers(self, tmp_path):
        jwt = forge_jwt(tmp_path)

        _, rule = self._preview_rule(jwt)
        assert rule["requires"] == {
            "requires_any": {
                "requirements": [
                    {"provider_name": "keycloak_preview"},
                    {"provider_name": "workload_preview"},
                ]
            }
        }
        for name in ("keycloak", "workload"):
            preview = jwt["providers"][f"{name}_preview"]
            assert "from_params" not in preview
            base = dict(jwt["providers"][name])
            assert base.pop("from_params") == ["token"]
            assert preview == base

    def test_the_rule_matches_preview_hosts_only(self, tmp_path):
        _, rule = self._preview_rule(forge_jwt(tmp_path))

        (header,) = rule["match"]["headers"]
        pattern = re.compile(header["string_match"]["safe_regex"]["regex"])
        assert rule["match"]["prefix"] == "/"
        for host in (
            "5173--3f1c2d4e-0000-4000-8000-000000000000.preview.niuu.example",
            "8000--abc.preview.niuu.example:443",
        ):
            assert pattern.fullmatch(host), host
        for host in (
            "preview.niuu.example",
            "a.b.preview.niuu.example",
            "forge.niuu.example",
            "5173--abc.previewxniuu.example",
            "5173--abc.preview.niuu.example.evil.test",
        ):
            assert not pattern.fullmatch(host), host

    def test_it_runs_ahead_of_the_bypass_prefixes_and_the_catch_all(self, tmp_path):
        jwt = forge_jwt(tmp_path)

        index, _ = self._preview_rule(jwt)
        bypass = jwt["rules"].index({"match": {"prefix": "/health"}})
        assert jwt["rules"][0]["match"]["headers"][0]["name"] == ":method"
        assert index == 1 < bypass

    def test_without_workload_identity_in_envoy_only_keycloak_is_asked(self, tmp_path):
        jwt = forge_jwt(tmp_path, {"envoy": {"jwt": {"workload": {"enabled": False}}}})

        _, rule = self._preview_rule(jwt)
        assert rule["requires"] == {"provider_name": "keycloak_preview"}
        assert "workload_preview" not in jwt["providers"]

    def test_preview_off_adds_neither_providers_nor_the_rule(self, tmp_path):
        jwt = forge_jwt(tmp_path, {"sessionPreview": {"enabled": False}})

        assert list(jwt["providers"]) == ["keycloak", "workload"]
        assert not [
            rule
            for rule in jwt["rules"]
            if any(h["name"] == ":authority" for h in rule["match"].get("headers", []))
        ]
