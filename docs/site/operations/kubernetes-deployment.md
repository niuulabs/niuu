# Deploy Niuu on Kubernetes

Kubernetes deployment requires environment-specific configuration. The repository's
umbrella chart includes defaults for existing Niuu infrastructure; it is not a
self-contained installation of a database, identity provider, and secret system.
Do not deploy those defaults unchanged into a new cluster.

## Prerequisites

Use a checkout or chart version matching the application version you intend to
run. You need Helm, access to the target cluster, and a values file defining the
enabled services, database connections, identity, secrets, storage, and ingress.
Before applying anything, verify the selected context:

```bash
kubectl config current-context
helm show values ./charts/niuu
```

These commands inspect configuration. They do not create a deployment.

## Understand the values hierarchy

`charts/niuu/Chart.yaml` declares the subcharts. Values for each service are nested
under its dependency name; aliased Mímir dependencies have separate keys.

| Setting | Scope |
| --- | --- |
| `global.image.registry` | Shared image registry |
| `global.niuu.cluster` | Discovery location label |
| `volundr.database` | Völundr database connection and secret reference |
| `guild.database` | Guild database connection and secret reference |
| `volundr.podManager` | Backend that executes sessions |
| `ingress.hosts`, `ingress.routeSets`, `ingress.tls` | Umbrella ingress routing |
| Per-service `enabled` | Whether a dependency is deployed |

The old root-level `database.external.host` example did not configure Völundr's
subchart. Inspect each enabled service's values and templates; one database flag
is not a platform-wide database configuration.

## Render your configuration

Prepare `niuu-values.yaml` in your infrastructure checkout using the actual
endpoints, existing-secret names, storage classes, image versions, and hostnames
for your environment. Then, from the Niuu repository root:

```bash
helm dependency update ./charts/niuu
helm lint ./charts/niuu -f niuu-values.yaml
helm template niuu ./charts/niuu --namespace niuu   -f niuu-values.yaml > niuu-rendered.yaml
```

Inspect the rendered Deployments, Services, ingress routes, secret references,
PVCs, and migration containers. A successful render proves chart structure and
values can be rendered; it does not prove referenced secrets, databases, or
runtime profiles exist. Do not commit rendered secret values.

## Apply through your deployment owner

For a Helm-managed environment, after reviewing the rendered configuration:

```bash
helm upgrade --install niuu ./charts/niuu --namespace niuu --create-namespace   -f niuu-values.yaml --wait --timeout 10m
```

For GitOps, commit the reviewed values and pinned chart version to the owning
repository instead. Avoid a competing manual Helm release over resources owned
by a reconciler.

## Verify the result

```bash
helm status niuu --namespace niuu
kubectl --namespace niuu get deployments,pods,services,pvc
kubectl --namespace niuu get events --sort-by=.metadata.creationTimestamp
```

Inspect failed migration/init containers before retrying application pods. Verify
login through the public route, then create a real session on the selected target,
receive a model response, inspect a file, and stop the session. Test a second
identity's access and confirm the intended storage survives the relevant restart.

This documentation pass validates chart structure, not a live cluster rollout.
Use the [production acceptance checks](production-checklist.md) and
[OpenShell guide](openshell-runtime.md) for the chosen runtime.
