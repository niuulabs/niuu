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
POD_MANAGER__ADAPTER=volundr.adapters.outbound.openshell.OpenShellPodManager
POD_MANAGER__KWARGS__SANDBOX_IMAGE=ghcr.io/niuulabs/skuld:0.2.0
```

The local `niuu platform` CLI uses the `NIUU_` prefix for its own config and
then exports service-level settings when it starts Völundr.

Local development can use mini mode, cluster mode, or OpenShell mode. OpenShell
mode keeps the normal Skuld broker/session protocol but runs each session in an
OpenShell sandbox:

```yaml
pod_manager:
  adapter: "volundr.adapters.outbound.openshell.OpenShellPodManager"
  kwargs:
    openshell_binary: "openshell"
    gateway_url: ""
    gateway_name: local
    sandbox_image: "ghcr.io/niuulabs/skuld:0.2.0"
    cpu: "2"
    memory: "4Gi"
    policy_file: "/etc/niuu/openshell-policy.yaml"
    workspaces_dir: "~/.niuu/workspaces"
    state_file: "~/.niuu/openshell-forge-state.json"
    forward_mode: service
```

OpenShell-specific controls are split across three layers:

| Control | Where to configure it |
| --- | --- |
| CPU, memory, GPU | OpenShell sandbox create flags; Völundr exposes `cpu` and `memory` as adapter kwargs. |
| Network access | OpenShell policy YAML `network_policies`; pass the create-time policy with `policy_file`. |
| Filesystem/process access | OpenShell policy YAML `filesystem_policy`, `landlock`, and `process`; these are locked at sandbox creation. |
| Workspace storage and mounts | OpenShell driver config; Völundr exposes workspace bind/upload settings and `sandbox_mounts`. |
| CLI location | `openshell_binary`, or `NIUU_POD_MANAGER__OPENSHELL_BINARY` for local overrides. |

See [OpenShell runtime](../operations/openshell-runtime.md) for the local
OpenShell workflow and policy examples.

## Local stack

`./start-dev` sets the local host profile and aligns service URLs so embedded services can call back into the shared platform host.
