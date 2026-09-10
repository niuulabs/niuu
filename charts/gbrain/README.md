# gbrain

Deploy upstream gbrain as a standalone knowledge service. The image recipe at
`containers/gbrain/Dockerfile` pins upstream commit
`43597b19e50a3abf56409337f248f7966860293c` (0.48.5.0), with Bun 1.4.2.
Build and publish that image to your registry, then set `image.repository` and
`image.tag`. This chart does not assume a published Niuu gbrain image exists.

Create a Kubernetes Secret and set `existingSecret` to its name. It must contain
`GBRAIN_ADMIN_BOOTSTRAP_TOKEN`, at least 32 URL-safe characters. Generate this
credential securely; do not place it in Helm values or commit it. Provider keys
such as `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` may be included in the same Secret.
The admin bootstrap credential is not a Mimir API token: create a scoped gbrain
read/write token using gbrain's admin flow before configuring the Mimir adapter.

Install from this repository with `helm upgrade --install brain charts/gbrain
-f /path/to/your-values.yaml --namespace niuu --create-namespace --wait`.

The default is a single replica backed by PGLite and a 5Gi PVC. Upgrades use
Recreate to avoid two processes opening the database. The chart retains its PVC
on uninstall; delete it separately only when its data is no longer needed.
An existing claim can be supplied with `persistence.existingClaim`.

For external PostgreSQL, set `engine: postgres` and include
`GBRAIN_DATABASE_URL` in the Secret. Provision a database supported by upstream
gbrain, including its required extensions. The server initializes the schema
on first startup. Back up the database before image upgrades.

The service exposes port 3131 by default: `/health`, `/mcp`, and upstream `/admin`.
It is ClusterIP only; use your existing authenticated gateway for external
access. The chart does not install an ingress or expose the admin service publicly.

Embeddings are explicitly disabled on initial setup by default. To initialize
with embeddings, set `embedding.enabled`, `embedding.model` and
`embedding.dimensions`, plus the corresponding provider credentials. Changing
embedding dimensions after initialization requires an upstream migration/re-embed;
changing values alone does not migrate existing vectors. Native gbrain settings
can be supplied in `config`; credentials belong in the Secret. Storage settings
are established at initialization, so switching engines on an existing release
requires a deliberate data migration.

## Dream cycles

`dream.enabled: true` creates a CronJob, defaulting to 02:00 in the controller's
time zone. PostgreSQL is required because a separate job cannot safely share the
running PGLite store. Jobs do not overlap and failed jobs are not retried automatically.
The default phases are `lint`, `backlinks`, and `orphans`. Select additional phases
explicitly in `dream.phases`: `sync`, `synthesize`, `extract`, `patterns`, `embed`.
Synthesis and patterns need model configuration and a transcript corpus; a bare
server deployment does not create a corpus or ingest your accounts automatically.
Use database-backed maintenance here; filesystem transcript and git-sync workflows
need their own accessible source storage. Model phases can incur provider costs.

The chart does not start upstream autopilot in addition to the CronJob. Use one
scheduler for a given maintenance workload.

## Niuu integration

Connect through `ravn.adapters.mimir.gbrain.GBrainMimirAdapter`, using the service's
`/mcp` URL and a scoped API token. gbrain's native MCP endpoint is not the Mimir
HTTP API: a registry URL alone does not turn it into a Mimir mount. The existing
adapter supplies that boundary and enables graph projection.

UI provisioning is a separate control-plane feature: it must create a release,
track deployment readiness/failures, and attach the configured adapter. Merely
saving a registry entry does not deploy this chart.
