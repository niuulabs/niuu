# Configuration Reference

Understand how Niuu services are configured.

Services load YAML configuration with environment variable overrides. Nested environment overrides use `__`.

## Common config files

| Service | Config file |
| --- | --- |
| Völundr | `config.yaml` or `/etc/volundr/config.yaml` |
| Bifröst | `bifrost.yaml` |
| Ting | `ting.yaml` |
| Ravn | `ravn.yaml` |

## Environment overrides

```bash
DATABASE__HOST=postgres.local
DATABASE__PASSWORD=secret
GIT__GITHUB__TOKEN=ghp_xxxx
EVENT_PIPELINE__OTEL__ENABLED=true
```

## Völundr pod manager

Völundr launches Forge sessions through a dynamic `pod_manager` adapter. The
`adapter` value is a fully qualified Python class name. Values under `kwargs`
are passed to that adapter.

```yaml
pod_manager:
  adapter: "volundr.adapters.outbound.flux.FluxPodManager"
  kwargs:
    namespace: volundr
```

Environment overrides use the same nested shape. For a directly started
Völundr service, adapter kwargs live under `POD_MANAGER__KWARGS__...`:

```bash
POD_MANAGER__ADAPTER=volundr.adapters.outbound.openshell_gateway.OpenShellGatewayPodManager
POD_MANAGER__KWARGS__GATEWAY_ENDPOINT=openshell.openshell.svc.cluster.local:8080
```

The local `niuu platform` CLI uses the `NIUU_` prefix for its own config and
then exports service-level settings when it starts Völundr.

Deployments can use mini mode, cluster mode, or OpenShell mode. OpenShell mode
keeps the normal Skuld broker/session protocol but creates each session through
the OpenShell gateway API:

```yaml
pod_manager:
  adapter: "volundr.adapters.outbound.openshell_gateway.OpenShellGatewayPodManager"
  kwargs:
    gateway_endpoint: "openshell.openshell.svc.cluster.local:8080"
    token_url: "https://keycloak.niuu.world/realms/volundr/protocol/openid-connect/token"
    client_id: "openshell-volundr-agent"
    sandbox_image: "ghcr.io/niuulabs/niuu-openshell:openshell-provider-v2-20260709"
    sandbox_command: ["/opt/niuu/bin/python", "-m", "skuld"]
    service_port: 9200
  secret_kwargs_env:
    client_secret: OPENSHELL_OIDC_CLIENT_SECRET
```

OpenShell-specific controls are split across three layers:

| Control | Where to configure it |
| --- | --- |
| Authentication | Keycloak client credentials from `OPENSHELL_OIDC_CLIENT_SECRET`; the token is sent as `Authorization: Bearer ...` to the gateway. |
| CPU and memory | Session `resources` values; the adapter maps them to OpenShell template resources. |
| Scheduling | Session `nodeSelector`, `tolerations`, `runtimeClassName`, and `priorityClassName`; the adapter maps them to Kubernetes driver config. |
| Runtime env | Session env and pod-spec literal env values; secret `valueFrom` env requires a follow-up gateway-compatible secret path. |
| Service exposure | OpenShell `ExposeService`; Völundr stores the returned Skuld chat/code endpoints. |

See [OpenShell runtime](../operations/openshell-runtime.md) for the gateway
runtime shape.

## Local stack

`./start-dev` sets the local host profile and aligns service URLs so embedded services can call back into the shared platform host.
