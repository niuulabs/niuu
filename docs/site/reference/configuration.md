# Configuration

Configure the process you are starting. The Niuu host CLI, a standalone service,
and a Helm chart have related but different configuration schemas.

## Local Niuu host

`niuu platform init` writes the local host configuration. By default it uses
`~/.niuu/config.yaml`. Select another file with the root `--config` option or
`NIUU_CONFIG`. In the current source, initialization respects that selection.

```bash
niuu --config ./niuu.yaml platform init
niuu --config ./niuu.yaml platform up
```

Initialization asks before overwriting an existing file. The second command runs
in the foreground; stop it with Ctrl+C. Use a dedicated directory if you want an
independent configuration, and also choose separate database, workspace, and port
settings before running two hosts at once.

The host schema is `CLISettings` in `src/cli/config.py`. This is a valid local
configuration fragment to merge into the generated file:

```yaml
server:
  host: 127.0.0.1
  port: 8080
pod_manager:
  adapter: volundr.adapters.outbound.local_process.LocalProcessPodManager
  workspaces_dir: ~/.niuu/workspaces
  max_concurrent: 4
  sdk_port_start: 9100
```

The local host adapter's arguments sit directly under `pod_manager`. They are
passed to the selected adapter. `server.external_host` controls a browser-facing
host when it differs from the listen address; it does not configure TLS or login.

## Environment overrides

The Niuu CLI uses `NIUU_` and `__` for nested fields. For one foreground run on a
different port:

```bash
NIUU_SERVER__PORT=8081 niuu platform up
```

Explicit constructor settings take precedence over environment settings, which
take precedence over YAML in the CLI settings loader. When a value surprises you,
check the selected config file and relevant exported overrides. Do not print an
entire environment or configuration containing credentials into a shared log.

## Standalone services

Service settings belong to their own configuration models, such as
`src/volundr/config.py`, `src/ravn/config.py`, and `src/mimir/config.py`.
A standalone Völundr `pod_manager` uses `adapter` plus a nested `kwargs` mapping.
Do not paste the host's flattened adapter shape into that schema.

Secret-backed adapter arguments use explicit secret mappings. For OpenShell,
service configuration uses `secret_kwargs_env`; Helm uses `podManager.secretKwargs`.
See [OpenShell configuration](../operations/openshell-runtime.md).

## Helm values

Chart values are a third schema. Within the umbrella chart, Völundr values belong
under `volundr`, Ting values under `ting`, and so on. A root
`database.external.host` does not configure every subchart's database.

Inspect and render the selected chart version before deploying:

```bash
helm show values ./charts/niuu
```

Use [the Helm reference](helm-charts.md) for chart scope and
[deployment](../operations/kubernetes-deployment.md) for rendering and validation.
