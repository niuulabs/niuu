# Knowledge deployments through Guild

The Niuu registry discovers deployment targets from enabled **Mimir service**
entries visible to the signed-in user in Guild. These are the platform services
that manage deployments, not the individual Mimir or gbrain wells they create.
Register each platform Mimir service in Guild with its service URL. Root URLs,
`/api/v1`, and `/api/v1/mimir` URLs are supported.

Each service exposes the targets configured in its `deployment` adapter. This
can be one Flux target or `mimir.adapters.deployment_targets.KnowledgeDeploymentTargets`
combining local and remote targets. Storage class, chart/image versions and
warden profiles remain configured on the owning service. Guild does not store
or copy Kubernetes credentials. Requests reuse the signed-in user's identity
and the remote service enforces deployment administrator access.

The **Deploy to** selector combines those targets dynamically. Disabling or
removing a Guild entry removes its targets on the next refresh. Unreachable or
unconfigured services are shown with their errors. Names can repeat across
targets; inspection and lifecycle actions retain both the Guild service ID and
the service-local target ID.

A platform Mimir service using in-cluster Flux needs a projected Kubernetes
service-account token and namespace-scoped permissions for its HelmReleases and
runtime logs. Use the chart's `extraVolumes` and `extraVolumeMounts` for the
projected identity, as in the Ymir platform deployment. Ordinary knowledge
instances do not need Kubernetes access.

For gbrain, enable `postgres.enabled` with `engine: postgres` and choose
`postgres.storageClass` and `postgres.size`. CloudNativePG generates the app
credentials; the service and native dream jobs reference the generated Secret.
The chart manages the database owner's `BYPASSRLS` attribute, required by gbrain
schema migrations. This does not require a superuser or a Doppler credential.

Use **Inspect → Update** to move a Flux-managed instance to the chart/image
versions configured by its deployment target, preserving its database, dream
settings, and warden configuration.

Flux deployment targets supply the cluster and stable instance identity to both
charts. Observatory's existing Kubernetes discovery picks them up when its
namespace scope and label selector cover the deployment target. Each instance
keeps its own identity, health and service endpoint. Managed gbrain databases
carry the same cluster label and a separate database identity linked from the
service. For standalone Helm installs, set `niuu.cluster` (and optionally
`niuu.instanceId`); Mimir also honors the umbrella `global.niuu.cluster`.
