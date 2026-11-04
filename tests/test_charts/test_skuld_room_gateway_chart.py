"""Room gateway mode of the Skuld chart.

A room gateway release runs only the broker, behind the chart's Envoy sidecar
and Cedar ext_authz service, as an authenticated collaboration room for a Ravn
resident that runs elsewhere. These tests render the chart with ``helm
template`` and, for the ownership claims, drive the rendered authorization
config through the real Cedar engine.
"""

import copy
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import yaml
from envoy.service.auth.v3 import external_auth_pb2
from google.protobuf.json_format import ParseDict

from identity.adapters.cedar import CedarAuthorizationAdapter
from identity.adapters.envoy_authz import EnvoyAuthorizationService
from identity.authz_config import AuthorizationGatewayConfig
from skuld.config import SkuldSettings

CHART_DIR = Path(__file__).parent.parent.parent / "charts" / "skuld"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required")

OWNER = "11111111-2222-3333-4444-555555555555"
TENANT = "niuu"
OPERATOR_ISSUER = "https://auth.test/realms/niuu"
WORKLOAD_ISSUER = "https://yggdrasil.niuu.world/api/v1/tokens/workload"

BASE_VALUES = {
    "roomGateway": {
        "enabled": True,
        "routeId": "regin",
        "room": {"environmentId": "niuu", "defaultTargetPeerId": "regin"},
        "ingress": {
            "host": "yggdrasil.niuu.world",
            "tls": {"secretName": "ymir-niuu-tls"},
        },
    },
    "session": {
        "id": "regin-operator-room",
        "name": "Regin operator room",
        "ownerId": OWNER,
        "tenantId": TENANT,
    },
    "envoy": {
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
                "jwksHost": "yggdrasil.niuu.world",
            },
        }
    },
}


def merged(base: dict, overrides: dict | None) -> dict:
    result = copy.deepcopy(base)
    for key, value in (overrides or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merged(result[key], value)
            continue
        result[key] = value
    return result


def helm_template(tmp_path: Path, overrides: dict | None = None, *, release="regin-room"):
    values_file = tmp_path / f"{release}-values.yaml"
    values_file.write_text(yaml.safe_dump(merged(BASE_VALUES, overrides)))
    return subprocess.run(
        ["helm", "template", release, str(CHART_DIR), "-f", str(values_file)],
        capture_output=True,
        text=True,
        check=False,
    )


def render(tmp_path: Path, overrides: dict | None = None, *, release="regin-room") -> list[dict]:
    result = helm_template(tmp_path, overrides, release=release)
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def of_kind(docs: list[dict], kind: str) -> list[dict]:
    return [doc for doc in docs if doc["kind"] == kind]


def configmap_file(docs: list[dict], filename: str) -> dict:
    return next(
        yaml.safe_load(doc["data"][filename])
        for doc in of_kind(docs, "ConfigMap")
        if filename in doc["data"]
    )


def envoy_http(docs: list[dict]) -> dict:
    config = configmap_file(docs, "envoy.yaml")
    return config["static_resources"]["listeners"][0]["filter_chains"][0]["filters"][0][
        "typed_config"
    ]


def pod(docs: list[dict]) -> dict:
    (deployment,) = of_kind(docs, "Deployment")
    return deployment["spec"]["template"]["spec"]


def container(docs: list[dict], name: str) -> dict:
    return next(c for c in pod(docs)["containers"] if c["name"] == name)


class TestRoomGatewayRender:
    def test_pod_is_broker_envoy_and_cedar_only(self, tmp_path):
        docs = render(tmp_path)

        spec = pod(docs)
        assert [c["name"] for c in spec["containers"]] == ["skuld", "cedar-authz", "envoy"]
        assert "initContainers" not in spec
        assert {v["name"] for v in spec["volumes"]} == {
            "authz-config",
            "skuld-config",
            "envoy-config",
            "data",
        }
        assert spec["automountServiceAccountToken"] is False
        kinds = sorted(doc["kind"] for doc in docs)
        assert kinds == [
            "ConfigMap",
            "ConfigMap",
            "ConfigMap",
            "Deployment",
            "Ingress",
            "Middleware",
            "Service",
        ]

    def test_no_resident_cli_ide_nginx_or_forge_plumbing(self, tmp_path):
        docs = render(
            tmp_path,
            {
                # Defaults and settings that would add these to an ordinary session pod.
                "localServices": {"enabled": True, "terminal": {"debug": True}},
                "homeVolume": {"enabled": True, "existingClaim": "home"},
                "git": {"repoUrl": "https://example.test/org/repo.git"},
                "secretManifest": {"files": []},
                "persistence": {"enabled": True},
            },
        )

        names = {c["name"] for c in pod(docs)["containers"]}
        assert names.isdisjoint({"nginx", "devrunner", "ravn", "code-server"})
        assert "initContainers" not in pod(docs)
        config_maps = {d["metadata"]["name"] for d in of_kind(docs, "ConfigMap")}
        assert config_maps == {
            "regin-room-skuld-authz",
            "regin-room-skuld-envoy",
            "regin-room-skuld-config",
        }
        broker = container(docs, "skuld")
        # The explicit command skips the image entrypoint's Forge-session
        # workspace setup; without it the broker exits on mkdir under
        # /volundr/sessions, which a room never mounts.
        assert broker["args"] == ["skuld"]
        assert broker["env"] == [{"name": "HOME", "value": "/var/lib/skuld"}]
        assert {m["name"] for m in broker["volumeMounts"]} == {"skuld-config", "data"}
        volume_claims = [v for v in pod(docs)["volumes"] if "persistentVolumeClaim" in v]
        assert volume_claims == []
        assert not of_kind(docs, "PersistentVolumeClaim")
        images = {c["image"] for c in pod(docs)["containers"]}
        assert images == {"ghcr.io/niuulabs/skuld:latest", "envoyproxy/envoy:v1.32-latest"}

        config = configmap_file(docs, "config.yaml")
        for cli_key in ("transport_adapter", "cli_binary", "codex_auth", "cli_type", "pi", "dsh"):
            assert cli_key not in config
        assert config["forge_mcp"] == {"enabled": False}
        assert config["event_log_enabled"] is False
        assert config["activity_reporting_enabled"] is False
        assert "volundr_api_url" not in config
        assert "persistent_home_path" not in config

    def test_broker_config_loads_as_a_room_without_a_cli(self, tmp_path):
        config = configmap_file(render(tmp_path), "config.yaml")

        settings = SkuldSettings(**config)

        assert settings.room.enabled is True
        assert settings.room.routed is True
        assert settings.room.environment_id == "niuu"
        assert settings.room.default_target_peer_id == "regin"
        assert settings.session.id == "regin-operator-room"
        assert settings.session.name == "Regin operator room"
        assert settings.session.owner_id == OWNER
        assert settings.session.tenant_id == TENANT
        assert settings.host == "127.0.0.1"
        assert settings.port == 8081
        assert settings.workspace_path == "/var/lib/skuld/workspace"
        assert settings.persistence_mount_path == "/var/lib/skuld"
        assert settings.mesh.enabled is False
        assert settings.reflex.enabled is False
        assert settings.forge_mcp.enabled is False
        assert settings.chronicle_watcher_enabled is False
        assert settings.chronicle_on_stop_enabled is False
        assert settings.ws_auth.enforce_ownership is True
        assert settings.ws_auth.allow_loopback is False
        assert settings.ws_auth.room_role_source == "deployment"
        assert settings.ws_auth.authorization.adapter == (
            "identity.adapters.cedar.CedarAuthorizationAdapter"
        )

    def test_session_id_defaults_to_the_route_id(self, tmp_path):
        docs = render(tmp_path, {"session": {"id": ""}})

        assert configmap_file(docs, "config.yaml")["session"]["id"] == "regin"
        assert configmap_file(docs, "authz.yaml")["session"]["id"] == "regin"

    def test_data_volume_is_an_emptydir_unless_a_claim_is_named(self, tmp_path):
        ephemeral = render(tmp_path)
        durable = render(
            tmp_path, {"roomGateway": {"persistence": {"existingClaim": "regin-room"}}}
        )

        assert {v["name"]: v for v in pod(ephemeral)["volumes"]}["data"] == {
            "name": "data",
            "emptyDir": {},
        }
        assert {v["name"]: v for v in pod(durable)["volumes"]}["data"]["persistentVolumeClaim"] == {
            "claimName": "regin-room"
        }

    def test_extra_volumes_back_the_envoy_mounts(self, tmp_path):
        docs = render(
            tmp_path,
            {
                "extraVolumes": [{"name": "ca", "configMap": {"name": "private-ca"}}],
                "envoy": {"extraVolumeMounts": [{"name": "ca", "mountPath": "/etc/ca"}]},
            },
        )

        assert {"name": "ca", "configMap": {"name": "private-ca"}} in pod(docs)["volumes"]
        assert {"name": "ca", "mountPath": "/etc/ca"} in container(docs, "envoy")["volumeMounts"]

    def test_data_path_moves_workspace_and_mount(self, tmp_path):
        docs = render(tmp_path, {"roomGateway": {"dataPath": "/srv/room/"}})

        config = configmap_file(docs, "config.yaml")
        assert config["persistence_mount_path"] == "/srv/room"
        assert config["session"]["workspace_dir"] == "/srv/room/workspace"
        mounts = {m["name"]: m["mountPath"] for m in container(docs, "skuld")["volumeMounts"]}
        assert mounts["data"] == "/srv/room"

    def test_config_change_rolls_the_pod(self, tmp_path):
        first = render(tmp_path)
        second = render(tmp_path, {"session": {"name": "Another name"}})

        def annotations(docs):
            (deployment,) = of_kind(docs, "Deployment")
            return deployment["spec"]["template"]["metadata"]["annotations"]

        assert annotations(first)["checksum/config"] != annotations(second)["checksum/config"]
        assert set(annotations(first)) >= {"checksum/config", "checksum/authz", "checksum/envoy"}

    def test_default_release_renders_no_room_objects(self, tmp_path):
        result = subprocess.run(
            ["helm", "template", "plain", str(CHART_DIR)],
            capture_output=True,
            text=True,
            check=True,
        )

        assert "room-gateway" not in result.stdout
        assert "local_broker" not in result.stdout


class TestRoomGatewayAuth:
    def test_no_unauthenticated_path_to_the_broker(self, tmp_path):
        docs = render(tmp_path)

        # Envoy is the only listener the Service reaches.
        (service,) = of_kind(docs, "Service")
        assert [p["targetPort"] for p in service["spec"]["ports"]] == ["envoy"]
        spec = pod(docs)
        ports = [p for c in spec["containers"] for p in c.get("ports", [])]
        assert ports == [{"name": "envoy", "containerPort": 8444, "protocol": "TCP"}]
        # The broker listens on loopback, the authorization service is told to
        # (identity.authz_main binds 127.0.0.1) and Envoy's admin port is loopback.
        config = configmap_file(docs, "config.yaml")
        assert config["host"] == "127.0.0.1"
        envoy = configmap_file(docs, "envoy.yaml")
        assert envoy["admin"]["address"]["socket_address"]["address"] == "127.0.0.1"
        clusters = {c["name"]: c for c in envoy["static_resources"]["clusters"]}
        broker = clusters["local_broker"]["load_assignment"]["endpoints"][0]["lb_endpoints"][0]
        assert broker["endpoint"]["address"]["socket_address"] == {
            "address": "127.0.0.1",
            "port_value": 8081,
        }
        assert "local_nginx" not in clusters

    def test_every_route_needs_a_jwt_and_cedar(self, tmp_path):
        http = envoy_http(render(tmp_path))

        assert [f["name"] for f in http["http_filters"]] == [
            "envoy.filters.http.lua",
            "envoy.filters.http.jwt_authn",
            "envoy.filters.http.ext_authz",
            "envoy.filters.http.router",
        ]
        routes = http["route_config"]["virtual_hosts"][0]["routes"]
        assert len(routes) == 1
        assert routes[0]["match"] == {"prefix": "/"}
        assert routes[0]["route"]["cluster"] == "local_broker"
        assert "typed_per_filter_config" not in routes[0]
        jwt = http["http_filters"][1]["typed_config"]
        # No /health, /ready or CORS-preflight rule: nothing is admitted without a token.
        assert jwt["rules"] == [
            {
                "match": {"prefix": "/"},
                "requires": {
                    "requires_any": {
                        "requirements": [
                            {"provider_name": "keycloak"},
                            {"provider_name": "workload"},
                        ]
                    }
                },
            }
        ]
        authz = http["http_filters"][2]["typed_config"]
        assert authz["failure_mode_allow"] is False
        assert authz["metadata_context_namespaces"] == ["envoy.filters.http.jwt_authn"]

    def test_both_token_carriers_are_accepted(self, tmp_path):
        jwt = envoy_http(render(tmp_path))["http_filters"][1]["typed_config"]
        providers = jwt["providers"]

        # Lexi sends Authorization: Bearer; web-next puts the token in ?token=.
        operator = providers["keycloak"]
        assert operator["from_headers"] == [{"name": "Authorization", "value_prefix": "Bearer "}]
        assert {"token", "access_token"} <= set(operator["from_params"])
        # The resident's Ravn sends Authorization: Bearer with its workload token.
        resident = providers["workload"]
        assert resident["from_headers"] == [{"name": "Authorization", "value_prefix": "Bearer "}]
        assert providers["workload"]["issuer"] == WORKLOAD_ISSUER
        assert providers["keycloak"]["issuer"] == OPERATOR_ISSUER
        assert providers["keycloak"]["forward"] is True

    def test_identity_headers_are_stripped_before_verification(self, tmp_path):
        http = envoy_http(render(tmp_path))
        lua = http["http_filters"][0]["typed_config"]["default_source_code"]["inline_string"]

        for header in ("x-auth-user-id", "x-auth-tenant", "x-auth-roles", "x-niuu-room-role"):
            assert f'handle:headers():remove("{header}")' in lua

    def test_workload_provider_is_optional(self, tmp_path):
        docs = render(tmp_path, {"envoy": {"jwt": {"workload": {"enabled": False}}}})

        jwt = envoy_http(docs)["http_filters"][1]["typed_config"]
        assert list(jwt["providers"]) == ["keycloak"]
        authz = AuthorizationGatewayConfig.model_validate(configmap_file(docs, "authz.yaml"))
        assert [p.issuer for p in authz.providers] == [OPERATOR_ISSUER]

    def test_auth_is_implied_without_envoy_or_ownership_flags(self, tmp_path):
        docs = render(
            tmp_path,
            {"envoy": {"enabled": False}, "wsAuth": {"enforce_ownership": False}},
        )

        assert container(docs, "envoy")
        assert container(docs, "cedar-authz")
        config = configmap_file(docs, "config.yaml")
        assert config["ws_auth"]["enforce_ownership"] is True

    def test_network_policy_selects_the_envoy_port(self, tmp_path):
        docs = render(tmp_path, {"networkPolicy": {"enabled": True}})

        (policy,) = of_kind(docs, "NetworkPolicy")
        assert policy["spec"]["ingress"][0]["ports"] == [{"protocol": "TCP", "port": 8444}]

    def test_probes_run_in_the_container_not_through_envoy(self, tmp_path):
        broker = container(render(tmp_path), "skuld")

        for probe, path in (("readinessProbe", "/ready"), ("livenessProbe", "/health")):
            command = broker[probe]["exec"]["command"]
            assert command[:2] == ["python", "-c"]
            assert f"http://127.0.0.1:8081{path}" in command[2]
        assert "httpGet" not in broker["readinessProbe"]

    @pytest.mark.parametrize(
        "overrides, remedy",
        [
            ({"envoy": {"jwt": {"enabled": False}}}, "envoy.jwt.enabled=true"),
            ({"envoy": {"jwt": {"issuer": ""}}}, "envoy.jwt.issuer"),
            ({"envoy": {"jwt": {"audiences": []}}}, "envoy.jwt.audiences"),
            ({"envoy": {"jwt": {"jwksUri": ""}}}, "https envoy.jwt.jwksUri"),
            (
                {"envoy": {"jwt": {"jwksUri": "http://auth.test/certs"}}},
                "https envoy.jwt.jwksUri",
            ),
            ({"envoy": {"jwt": {"keycloakTls": False}}}, "envoy.jwt.keycloakTls=true"),
            ({"envoy": {"jwt": {"keycloakHost": ""}}}, "envoy.jwt.keycloakHost"),
            (
                {"envoy": {"jwt": {"workload": {"jwksTls": False}}}},
                "envoy.jwt.workload.jwksTls=true",
            ),
            (
                {"envoy": {"jwt": {"workload": {"jwksUri": "http://w.test/jwks"}}}},
                "https envoy.jwt.workload.jwksUri",
            ),
            ({"envoy": {"jwt": {"workload": {"issuer": ""}}}}, "envoy.jwt.workload.issuer"),
        ],
    )
    def test_enabling_without_jwt_fails_with_the_remedy(self, tmp_path, overrides, remedy):
        result = helm_template(tmp_path, overrides)

        assert result.returncode != 0
        assert remedy in result.stderr

    def test_ownership_cannot_be_weakened(self, tmp_path):
        loopback = helm_template(tmp_path, {"wsAuth": {"allow_loopback": True}})
        remote = helm_template(tmp_path, {"wsAuth": {"room_role_source": "remote"}})
        allow_all = helm_template(
            tmp_path,
            {
                "wsAuth": {
                    "authorization": {
                        "adapter": "identity.adapters.authorization.AllowAllAuthorizationAdapter"
                    }
                }
            },
        )

        assert loopback.returncode != 0
        assert "wsAuth.allow_loopback=false" in loopback.stderr
        assert remote.returncode != 0
        assert "wsAuth.room_role_source=deployment" in remote.stderr
        assert allow_all.returncode != 0
        assert "requires a resource authorization adapter" in allow_all.stderr

    @pytest.mark.parametrize(
        "overrides, remedy",
        [
            ({"roomGateway": {"routeId": ""}}, "roomGateway.routeId is required"),
            ({"roomGateway": {"routeId": "Not_A_Label"}}, "lowercase DNS-style label"),
            ({"roomGateway": {"room": {"environmentId": ""}}}, "roomGateway.room.environmentId"),
            (
                {"roomGateway": {"room": {"defaultTargetPeerId": ""}}},
                "roomGateway.room.defaultTargetPeerId",
            ),
            ({"session": {"ownerId": ""}}, "session.ownerId is required"),
            ({"session": {"tenantId": ""}}, "session.tenantId is required"),
            ({"replicaCount": 2}, "replicaCount of 0 or 1"),
            ({"gateway": {"enabled": True}}, "gateway.enabled=true"),
            ({"resident": {"enabled": True, "persona": "p"}}, "resident.enabled=true"),
            ({"roomGateway": {"dataPath": "var/skuld"}}, "must be an absolute path"),
        ],
    )
    def test_missing_or_conflicting_settings_fail_with_the_remedy(
        self, tmp_path, overrides, remedy
    ):
        result = helm_template(tmp_path, overrides)

        assert result.returncode != 0
        assert remedy in result.stderr


def _claims(provider: str, **overrides) -> dict:
    roles = ["volundr:developer"]
    claims = {
        "iss": OPERATOR_ISSUER if provider == "operator" else WORKLOAD_ISSUER,
        "aud": ["volundr"] if provider == "operator" else ["volundr-api"],
        "exp": time.time() + 300,
        "sub": OWNER,
        "tenant_id": TENANT,
        "resource_access": (
            {"volundr": {"roles": roles}}
            if provider == "operator"
            else {"volundr-api": {"roles": roles}, "volundr": {"roles": roles}}
        ),
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


class TestRoomGatewayOwnership:
    """The rendered ext_authz config, through the real Cedar engine.

    The resident's workload token is minted by niuu.domain.services.workload_identity:
    ``sub`` is the mapped (or delegated) owner id, ``tenant_id`` its tenant, and the
    roles sit under ``resource_access.<audience>.roles``. Ownership holds exactly
    when that ``sub`` is the room's ``session.ownerId``.
    """

    @pytest.fixture
    def gateway(self, tmp_path):
        config = AuthorizationGatewayConfig.model_validate(
            configmap_file(render(tmp_path), "authz.yaml")
        )
        return EnvoyAuthorizationService(CedarAuthorizationAdapter(), config)

    async def _status(self, gateway, claims, path):
        response = await gateway.Check(_check_request(claims, path), None)
        if response.HasField("ok_response"):
            return 200
        return response.denied_response.status.code

    async def test_operator_token_reaches_the_session_socket(self, gateway):
        assert await self._status(gateway, _claims("operator"), "/session") == 200

    async def test_resident_workload_token_reaches_the_ravn_socket(self, gateway):
        assert await self._status(gateway, _claims("workload"), "/ws/ravn/regin") == 200

    async def test_workload_token_of_another_owner_is_refused(self, gateway):
        claims = _claims("workload", sub="99999999-0000-0000-0000-000000000000")

        assert await self._status(gateway, claims, "/ws/ravn/regin") == 403

    async def test_operator_of_another_owner_is_refused(self, gateway):
        claims = _claims("operator", sub="99999999-0000-0000-0000-000000000000")

        assert await self._status(gateway, claims, "/session") == 403

    async def test_other_tenant_is_refused_even_as_the_owner(self, gateway):
        for provider in ("operator", "workload"):
            claims = _claims(provider, tenant_id="another-tenant")
            assert await self._status(gateway, claims, "/session") == 403

    async def test_tenant_administrator_is_admitted(self, gateway):
        admin = ["volundr:admin"]
        claims = _claims(
            "operator",
            sub="99999999-0000-0000-0000-000000000000",
            resource_access={"volundr": {"roles": admin}},
        )

        assert await self._status(gateway, claims, "/session") == 200

    async def test_owner_without_a_developer_role_is_refused(self, gateway):
        claims = _claims("workload", resource_access={"volundr-api": {"roles": ["volundr:viewer"]}})

        assert await self._status(gateway, claims, "/ws/ravn/regin") == 403

    async def test_scoped_workload_credential_is_refused(self, gateway):
        # A token minted with scopes (token_use valkyrie_build) is admitted only at
        # the entry points that name its scope; the room names none.
        claims = _claims("workload", token_use="valkyrie_build", scopes=["forge:session:create"])

        assert await self._status(gateway, claims, "/ws/ravn/regin") == 403

    @pytest.mark.parametrize(
        "claims",
        [
            {"iss": "https://elsewhere.test"},
            {"aud": ["not-this-service"]},
            {"exp": 1},
        ],
    )
    async def test_unrecognised_tokens_are_refused(self, gateway, claims):
        assert await self._status(gateway, _claims("operator", **claims), "/session") == 401


class TestRoomGatewayExposure:
    def test_shared_host_path_is_stripped_before_the_broker(self, tmp_path):
        docs = render(tmp_path)

        (ingress,) = of_kind(docs, "Ingress")
        (middleware,) = of_kind(docs, "Middleware")
        assert ingress["spec"]["ingressClassName"] == "traefik"
        assert ingress["spec"]["tls"] == [
            {"hosts": ["yggdrasil.niuu.world"], "secretName": "ymir-niuu-tls"}
        ]
        (rule,) = ingress["spec"]["rules"]
        assert rule["host"] == "yggdrasil.niuu.world"
        (path,) = rule["http"]["paths"]
        assert path["path"] == "/rooms/regin"
        assert path["pathType"] == "Prefix"
        assert path["backend"]["service"] == {
            "name": "regin-room-skuld",
            "port": {"name": "http"},
        }
        assert middleware["spec"] == {"stripPrefix": {"prefixes": ["/rooms/regin"]}}
        assert ingress["metadata"]["annotations"] == {
            "traefik.ingress.kubernetes.io/router.middlewares": (
                f"default-{middleware['metadata']['name']}@kubernetescrd"
            )
        }

    def test_service_reaches_envoy_so_both_socket_paths_are_authenticated(self, tmp_path):
        docs = render(tmp_path)

        (service,) = of_kind(docs, "Service")
        assert service["spec"]["ports"][0]["port"] == 8080
        assert service["spec"]["ports"][0]["targetPort"] == "envoy"
        # wss://<host>/rooms/regin/session and /rooms/regin/ws/ravn/<peer> strip to the
        # broker's /session and /ws/ravn/<peer> (skuld.broker_api).
        broker_api = (CHART_DIR.parent.parent / "src" / "skuld" / "broker_api.py").read_text()
        assert '@app.websocket("/session")' in broker_api
        assert '@app.websocket("/ws/ravn/{peer_id}")' in broker_api

    def test_rooms_on_one_host_do_not_collide(self, tmp_path):
        regin = render(tmp_path, release="regin-room")
        other = render(
            tmp_path,
            {"roomGateway": {"routeId": "muninn"}, "session": {"id": "muninn-room"}},
            release="muninn-room",
        )

        def paths(docs):
            (ingress,) = of_kind(docs, "Ingress")
            return (
                ingress["spec"]["rules"][0]["host"],
                ingress["spec"]["rules"][0]["http"]["paths"][0]["path"],
                ingress["metadata"]["name"],
                of_kind(docs, "Middleware")[0]["metadata"]["name"],
            )

        regin_host, regin_path, regin_name, regin_strip = paths(regin)
        other_host, other_path, other_name, other_strip = paths(other)
        assert regin_host == other_host == "yggdrasil.niuu.world"
        assert regin_path == "/rooms/regin"
        assert other_path == "/rooms/muninn"
        assert regin_name != other_name
        assert regin_strip != other_strip

    def test_the_shared_tls_secret_is_not_taken_over_by_cert_manager(self, tmp_path):
        (ingress,) = of_kind(render(tmp_path), "Ingress")

        annotations = ingress["metadata"]["annotations"]
        assert not [key for key in annotations if key.startswith("cert-manager.io")]

    def test_operator_annotations_and_middleware_chain_are_kept(self, tmp_path):
        docs = render(
            tmp_path,
            {
                "roomGateway": {
                    "ingress": {
                        "annotations": {
                            "traefik.ingress.kubernetes.io/router.entrypoints": "websecure",
                            "traefik.ingress.kubernetes.io/router.middlewares": (
                                "default-cors@kubernetescrd"
                            ),
                        }
                    }
                }
            },
        )

        (ingress,) = of_kind(docs, "Ingress")
        annotations = ingress["metadata"]["annotations"]
        assert annotations["traefik.ingress.kubernetes.io/router.entrypoints"] == "websecure"
        assert annotations["traefik.ingress.kubernetes.io/router.middlewares"] == (
            "default-regin-room-skuld-strip@kubernetescrd,default-cors@kubernetescrd"
        )

    def test_path_prefix_is_configurable(self, tmp_path):
        docs = render(tmp_path, {"roomGateway": {"ingress": {"pathPrefix": "/chat/rooms"}}})

        (ingress,) = of_kind(docs, "Ingress")
        assert ingress["spec"]["rules"][0]["http"]["paths"][0]["path"] == "/chat/rooms/regin"
        assert of_kind(docs, "Middleware")[0]["spec"]["stripPrefix"]["prefixes"] == [
            "/chat/rooms/regin"
        ]

    def test_dedicated_host_mode_has_no_prefix_to_strip(self, tmp_path):
        docs = render(
            tmp_path,
            {"roomGateway": {"ingress": {"mode": "host", "host": "regin.niuu.world"}}},
        )

        (ingress,) = of_kind(docs, "Ingress")
        assert not of_kind(docs, "Middleware")
        assert ingress["spec"]["rules"][0]["host"] == "regin.niuu.world"
        assert ingress["spec"]["rules"][0]["http"]["paths"][0]["path"] == "/"
        assert "annotations" not in ingress["metadata"]

    def test_tls_can_be_left_to_a_terminating_proxy(self, tmp_path):
        docs = render(tmp_path, {"roomGateway": {"ingress": {"tls": {"enabled": False}}}})

        (ingress,) = of_kind(docs, "Ingress")
        assert "tls" not in ingress["spec"]

    def test_ingress_can_be_disabled_for_service_only_access(self, tmp_path):
        docs = render(tmp_path, {"roomGateway": {"ingress": {"enabled": False, "host": ""}}})

        assert not of_kind(docs, "Ingress")
        assert not of_kind(docs, "Middleware")
        assert of_kind(docs, "Service")

    @pytest.mark.parametrize(
        "ingress, remedy",
        [
            ({"host": ""}, "roomGateway.ingress.host is required"),
            ({"tls": {"secretName": ""}}, "roomGateway.ingress.tls.secretName is required"),
            ({"pathPrefix": "/rooms/"}, "must start with '/' and not end with '/'"),
            ({"mode": "subdomain"}, "must be path"),
        ],
    )
    def test_incomplete_exposure_fails_with_the_remedy(self, tmp_path, ingress, remedy):
        result = helm_template(tmp_path, {"roomGateway": {"ingress": ingress}})

        assert result.returncode != 0
        assert remedy in result.stderr

    def test_plain_session_ingress_is_unchanged_by_the_mode(self, tmp_path):
        result = subprocess.run(
            [
                "helm",
                "template",
                "plain",
                str(CHART_DIR),
                "--set",
                "ingress.mode=path",
                "--set",
                "session.id=abc",
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        docs = [doc for doc in yaml.safe_load_all(result.stdout) if doc]
        (ingress,) = of_kind(docs, "Ingress")
        assert ingress["spec"]["rules"][0]["http"]["paths"][0]["path"] == "/s/abc"


class TestRoomGatewayTelegram:
    def test_off_by_default(self, tmp_path):
        docs = render(tmp_path)

        config = configmap_file(docs, "config.yaml")
        assert config["telegram"] == {"enabled": False}
        assert [e["name"] for e in container(docs, "skuld")["env"]] == ["HOME"]

    def test_on_reads_the_bot_from_a_named_secret(self, tmp_path, monkeypatch):
        docs = render(
            tmp_path,
            {
                "roomGateway": {
                    "telegram": {
                        "enabled": True,
                        "secretName": "regin-room-telegram",
                        "notifyOnly": True,
                        "topicMode": "fixed_topic",
                        "messageThreadId": 7,
                    }
                }
            },
        )

        env = {
            e["name"]: e["valueFrom"]["secretKeyRef"]
            for e in container(docs, "skuld")["env"]
            if "valueFrom" in e
        }
        assert env == {
            "SKULD__TELEGRAM__BOT_TOKEN": {"name": "regin-room-telegram", "key": "bot-token"},
            "SKULD__TELEGRAM__CHAT_ID": {"name": "regin-room-telegram", "key": "chat-id"},
        }
        config = configmap_file(docs, "config.yaml")
        assert config["telegram"] == {
            "enabled": True,
            "notify_only": True,
            "topic_mode": "fixed_topic",
            "message_thread_id": 7,
        }
        # The credentials never enter the ConfigMap; the broker's own settings
        # loader joins them to the rendered file.
        monkeypatch.setenv("SKULD__TELEGRAM__BOT_TOKEN", "test-bot-token")
        monkeypatch.setenv("SKULD__TELEGRAM__CHAT_ID", "-100123")
        telegram = SkuldSettings(**config).telegram
        assert (telegram.enabled, telegram.notify_only) == (True, True)
        assert (telegram.topic_mode, telegram.message_thread_id) == ("fixed_topic", 7)
        assert (telegram.bot_token, telegram.chat_id) == ("test-bot-token", "-100123")

    def test_secret_keys_are_configurable(self, tmp_path):
        docs = render(
            tmp_path,
            {
                "roomGateway": {
                    "telegram": {
                        "enabled": True,
                        "secretName": "tg",
                        "botTokenKey": "token",
                        "chatIdKey": "chat",
                    }
                }
            },
        )

        keys = {
            e["name"]: e["valueFrom"]["secretKeyRef"]["key"]
            for e in container(docs, "skuld")["env"]
            if "valueFrom" in e
        }
        assert keys == {"SKULD__TELEGRAM__BOT_TOKEN": "token", "SKULD__TELEGRAM__CHAT_ID": "chat"}

    def test_thread_zero_is_a_thread_not_an_absent_one(self, tmp_path):
        docs = render(
            tmp_path,
            {
                "roomGateway": {
                    "telegram": {
                        "enabled": True,
                        "secretName": "tg",
                        "topicMode": "fixed_topic",
                        "messageThreadId": 0,
                    }
                }
            },
        )

        assert configmap_file(docs, "config.yaml")["telegram"]["message_thread_id"] == 0

    @pytest.mark.parametrize(
        "telegram, remedy",
        [
            ({"secretName": ""}, "roomGateway.telegram.secretName is required"),
            ({"topicMode": "everywhere"}, "must be one of shared_chat"),
            ({"topicMode": "fixed_topic"}, "requires roomGateway.telegram.messageThreadId"),
        ],
    )
    def test_incomplete_telegram_fails_with_the_remedy(self, tmp_path, telegram, remedy):
        result = helm_template(
            tmp_path,
            {"roomGateway": {"telegram": {"enabled": True, "secretName": "tg", **telegram}}},
        )

        assert result.returncode != 0
        assert remedy in result.stderr


class TestRoomGatewayObservability:
    def test_absent_by_default(self, tmp_path):
        assert "observability" not in configmap_file(render(tmp_path), "config.yaml")

    def test_block_is_passed_through_to_the_broker_settings(self, tmp_path):
        block = {
            "enabled": True,
            "service_name": "skuld-regin-room",
            "trace_endpoint": "https://tempo.test:4317",
            "metric_endpoint": "https://mimir.test/otlp/v1/metrics",
            "log_endpoint": "https://loki.test/otlp/v1/logs",
            "metric_export_interval_milliseconds": 5000,
        }
        config = configmap_file(
            render(tmp_path, {"roomGateway": {"observability": block}}), "config.yaml"
        )

        assert config["observability"] == block
        observability = SkuldSettings(**config).observability
        assert observability.enabled is True
        assert observability.service_name == "skuld-regin-room"
        assert observability.metric_export_interval_milliseconds == 5000
