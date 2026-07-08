# OpenShell Runtime

OpenShell mode runs each local Forge session inside an NVIDIA OpenShell
sandbox while keeping the existing Skuld broker and Forge session protocol.
Use it when local process mode is too permissive and a full Kubernetes cluster
is more machinery than the job needs.

## Runtime Shape

```text
Völundr session
  -> OpenShellPodManager
  -> OpenShell sandbox
  -> Skuld broker
  -> Claude, Codex, or another Skuld-managed CLI
```

This is still Skuld-on-OpenShell. Native NemoClaw, OpenClaw, Hermes, or
DeepAgents runtimes should use their own OpenShell/NemoClaw images and remain a
separate integration path.

## Configure The Pod Manager

OpenShell mode uses the same dynamic `pod_manager` adapter shape as mini and
cluster modes:

```yaml
pod_manager:
  adapter: "volundr.adapters.outbound.openshell.OpenShellPodManager"
  kwargs:
    openshell_binary: "openshell"
    gateway_url: ""
    gateway_name: local
    sandbox_image: "ghcr.io/niuulabs/skuld:0.2.0"
    workspaces_dir: "~/.niuu/workspaces"
    state_file: "~/.niuu/openshell-forge-state.json"
    sdk_port_start: 9200
    forward_mode: service
```

For local development, `./start-dev --openshell` applies these defaults. Override
individual values with `NIUU_POD_MANAGER__...` environment variables:

```bash
NIUU_POD_MANAGER__OPENSHELL_BINARY=/opt/openshell/bin/openshell \
NIUU_POD_MANAGER__SANDBOX_IMAGE=volundr-skuld:openshell-local \
  ./start-dev --openshell
```

## Sandbox Resources

CPU and memory are OpenShell sandbox-create settings. The Völundr adapter passes
them through to `openshell sandbox create --cpu ... --memory ...`.

```yaml
pod_manager:
  kwargs:
    cpu: "2"
    memory: "4Gi"
```

Docker and Podman apply these as runtime limits. Kubernetes applies them as both
request and limit. The OpenShell VM driver currently accepts these flags but
does not resize the VM allocation.

GPU requests are also an OpenShell sandbox-create concern, but Völundr does not
currently expose a first-class `gpu` adapter option.

## Policy Controls

OpenShell policy YAML controls filesystem access, process identity, and network
egress.

Static policy sections are locked at sandbox creation:

- `filesystem_policy`
- `landlock`
- `process`

Dynamic policy sections can be hot-reloaded on a running sandbox with
`openshell policy update` or `openshell policy set`:

- `network_policies`

Attach a create-time policy file through the adapter:

```yaml
pod_manager:
  kwargs:
    policy_file: "/etc/niuu/openshell-policy.yaml"
```

Example policy:

```yaml
version: 1

filesystem_policy:
  include_workdir: true
  read_only: [/usr, /lib, /etc, /var/log]
  read_write: [/sandbox, /tmp]
landlock:
  compatibility: best_effort
process:
  run_as_user: sandbox
  run_as_group: sandbox

network_policies:
  github_api:
    endpoints:
      - host: api.github.com
        port: 443
        protocol: rest
        enforcement: enforce
        access: read-only
    binaries:
      - path: /usr/bin/curl
```

Network policies are per binary and per endpoint. If no policy entry matches
the destination and calling binary, outbound traffic is denied. REST and
WebSocket policies can enforce method/path rules instead of only host/port
rules.

## Workspace Storage And Mounts

OpenShell storage behavior depends on the active compute driver.

Docker and Podman support `volume`, `tmpfs`, and opt-in host `bind` mounts
through driver config. Bind mounts require `enable_bind_mounts = true` in the
OpenShell gateway driver config and can weaken isolation by exposing host paths
to the sandbox.

The Völundr adapter exposes the common local workflow settings:

```yaml
pod_manager:
  kwargs:
    mount_workspace: true
    sandbox_mounts: "~/.codex:/home/sandbox/.codex"
```

Use upload mode when bind mounts are not available or when the sandbox needs a
writable copy of the checkout:

```yaml
pod_manager:
  kwargs:
    mount_workspace: false
    upload_workspace: true
    upload_workspace_target: "/sandbox/workspace"
    sandbox_workspace: "/sandbox/workspace/volundr"
```

## Managed Inference

OpenShell can route sandbox LLM calls through `https://inference.local`. In that
mode the gateway injects provider credentials and strips sandbox-supplied
credentials before forwarding requests upstream.

The current Völundr OpenShell adapter does not yet configure managed inference.
Today it preserves the existing Skuld behavior and passes normal CLI/model
environment into the sandbox. Managed inference through OpenShell should be a
follow-up hardening step.

## Stop And Clean Up

`./stop-dev` stops the local stack and removes OpenShell sandboxes tracked in
the local OpenShell state file:

```text
~/.niuu/openshell-forge-state.json
```

Use OpenShell directly when debugging:

```bash
openshell status
openshell sandbox list -o json
openshell sandbox get forge-<session-id>
openshell logs forge-<session-id> --source sandbox
```
