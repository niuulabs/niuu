# Find service instances and inspect topology

Use Guild when more than one instance of a service is available. Use Observatory
to understand relationships and activity across those instances.

## Inspect the registered instances

With a local platform running:

```bash
curl --fail --silent --show-error http://127.0.0.1:8080/api/v1/niuu/instances
curl --fail --silent --show-error http://127.0.0.1:8080/api/v1/niuu/targets/volundr
```

Open **Guild** in the UI and match the service instance, endpoint, and location to
the workload you intend to use. Guild groups instances of the same service; it is
not the agent's tool or capability registry.

An instance record is not proof that its endpoint can be reached by every caller.
Check reachability from the host that will route the request, and check the
identity expected by the destination.

## How Guild probes health

Guild probes each registered instance on registration and on a periodic interval
(`niuu.health.interval_seconds`), and records `health` (`unknown` / `ok` /
`unreachable`), `lastSeenAt`, `lastCheckedAt`, and `lastError` on the instance —
visible in Guild's instance detail view and over the instances API.

The probe never guesses `{base_url}/health`. Two real deployment shapes break on a
bare `/health`:

- A standalone service whose ingress only routes its own `/api/v1/<service>`
  prefix — a bare `/health` never reaches the pod, so a healthy instance reports
  `unreachable` forever.
- A host shared with the web-next SPA, whose nginx also answers a bare `/health`
  itself (`containers/niuu-web/nginx.conf`) — the probe gets a 200 from the
  frontend's static health check and reports `ok` regardless of whether the actual
  backend is up.

Instead, the probe resolves a path per instance **kind**, appended to
`instance.base_url` — the path under that service's own API prefix, so it reaches
the real backend through either ingress shape:

| Kind | Default health path |
| --- | --- |
| `volundr` | `/api/v1/forge/health` |
| `ting` | `/api/v1/ting/health` |
| `mimir` | `/api/v1/mimir/health` |
| `bifrost` | `/api/v1/bifrost/health` |
| `ravn` | `/api/v1/ravn/health` |
| `observatory` | `/api/v1/observatory/health` |
| `generic`, or any kind not listed above | `/health` |

(`niuu.adapters.outbound.http_instance_probe.DEFAULT_HEALTH_PATHS` is the source of
truth — this table mirrors it.)

### Overriding a path

Two levels, evaluated in order:

1. **Per-kind, cluster-wide** — `niuu.health.probe.health_paths` in Guild's config
   (rendered from `registry.health.probe.healthPaths` in the `guild` chart's
   values) is a map of kind → path, merged on top of the built-in defaults. Set
   only the kinds you're changing:

   ```yaml
   registry:
     health:
       probe:
         healthPaths:
           mimir: "/mimir/health"
   ```

2. **Per instance** — `config.health_path` on one registered instance wins over
   both the per-kind override and the built-in default. Use this when one
   instance's `base_url` doesn't follow its kind's usual convention — for example
   an instance registered with the API version prefix already baked into
   `base_url` (`https://host/api/v1`), where the kind's normal
   `/api/v1/<service>/health` default would double up the prefix. Set it in the
   instance's `config` when registering or editing it in Guild.

A path that isn't publicly reachable through the target's Envoy sidecar reports
`unreachable` even though the service is healthy — Envoy's `jwt_authn` filter
requires a token on every route except `envoy.jwt.bypassPrefixes`. Each service's
own health path is already listed there in its chart's `values.yaml`; adding a new
per-kind or per-instance override that points somewhere else needs the same
Envoy bypass, or the probe will see 401/403 instead of the service's own health
check.

## Follow one session

Launch a session on a known target, then inspect **Observatory**. Follow the
session to its owning Forge instance and the relationships the deployment reports.
Compare those with the instance and target records. Missing relationships need
registration or telemetry investigation; they should not be inferred from names.

Cluster and namespace metadata should come from deployment configuration. The
umbrella chart exposes `global.niuu.cluster` for the cluster label. Use names that
reflect your deployment, not the infrastructure names from another installation.

## Diagnose the distinction

| Symptom | Likely layer to inspect |
| --- | --- |
| No instance is listed | Registration and service enablement |
| Instance exists but cannot be called | Endpoint, network, TLS, and authentication |
| Service works but target is unavailable | Advertised runtime profiles and target eligibility |
| Work succeeds but graph is incomplete | Relationship publication and observability ingestion |

[Observability](../operations/observability.md) continues from the graph to logs
and traces. [Architecture](../concepts/platform-model.md) distinguishes service
discovery from mesh membership and A2A discovery.
