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
