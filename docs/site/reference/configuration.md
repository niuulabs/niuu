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

### Authentication mode (`host_auth`)

Mini mode and docker mode have no Envoy JWT filter in front of them, so
`host_auth.mode` says explicitly how the host trusts a caller — it defaults
to `none` (today's behaviour: every caller is admin) and is never inferred.
It is named `host_auth`, not `auth`: Ting's own `Settings` reads a top-level
`auth:` key from this same file for an unrelated, differently-shaped config.

```yaml
host_auth:
  mode: none # or: oidc — see operations/security-and-permissions.md
  oidc:
    issuers:
      - issuer: "https://keycloak.example.com/realms/volundr"
        audiences: ["volundr-api"]
        jwks_uri: "" # empty = resolve via OIDC discovery; must be HTTPS
    clock_leeway_seconds: 60
    jwks_cache_ttl_seconds: 300
    jwks_timeout_seconds: 5.0
    min_refresh_interval_seconds: 5.0
    user_id_claim: sub
    email_claim: email
    tenant_claim: tenant_id
    roles_claim: resource_access.volundr.roles
```

`host_auth.mode: oidc` requires at least one `host_auth.oidc.issuers` entry
(issuer and one or more audiences); the CLI settings loader raises at
config-load time otherwise, as it does when a plugin or Bifröst mode not yet
covered by `oidc` (Mímir, Guild, an open Bifröst gateway) is still enabled.
See [Authentication mode on hosts without
Envoy](../operations/security-and-permissions.md#authentication-mode-on-hosts-without-envoy)
for what each mode covers today, path by path.

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

## Observability (OpenTelemetry)

Every process that owns a composition root — Völundr, Ting, Bifröst, Skuld,
Ravn, Mímir, Observatory, and the local `niuu` host itself
(`src/cli/shared_host.py`, the "Niuu Shared Services" app) — reads its own
`observability:` block from the shared
`niuu.domain.observability.ObservabilityConfig` shape:

```yaml
observability:
  enabled: true
  trace_endpoint: "http://otel-collector:4317"     # OTLP/gRPC
  metric_endpoint: "http://otel-collector:4318/v1/metrics"  # OTLP/HTTP
  insecure: true          # disable TLS on the gRPC trace exporter
  capture_content: false  # redacted, size-bounded event content on spans
  headers: {}              # optional OTLP headers (e.g. auth), both exporters
```

`enabled: true` requires both `trace_endpoint` and `metric_endpoint` — it fails
loudly at startup otherwise, per the no-fallbacks rule: a configured-but-broken
exporter must stop the process, not run quietly without traces. `enabled` false
(the default) is a decision, not a degraded fallback.

Each service's own `service_name` default (`volundr`, `ting`, `bifrost`, `skuld`,
`ravn`, `mimir`, `niuu-mini`) makes it identifiable in the trace backend without
you setting it. Observatory is the one exception — it reuses
`volundr.config.Settings` wholesale, so its `observability.service_name`
defaults to `volundr` too; set it explicitly in Observatory's own
`config.yaml` if you run both. The mini-mode host (`niuu platform up`) exposes the same block
under `CLISettings.observability` (`src/cli/config.py`) as the one place to
point the whole local stack at a collector; when several services share that
one process, only the first to configure wins the process-wide identity — see
`configure_observability`'s docstring in `src/niuu/observability.py`. See
[Diagnose an operation across services](../operations/observability.md) for
what one trace shows and how to point it at Tempo, Jaeger, or any other OTLP
collector.

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
