# Kubernetes And GitOps

Move to Kubernetes when local processes are no longer enough.

This step is for shared infrastructure: durable services, ingress, managed
secrets, resource limits, shared databases, and long-running assistants that
survive laptop restarts.

![Niuu architecture](../images/niuu-architecture.svg)

## When to move

Move when you need:

- other people or machines to use the platform
- persistent services
- shared ingress and TLS
- controlled secrets
- resource requests and limits
- observability
- resident assistants that should keep running
- GitOps-managed changes

Do not move just because Kubernetes exists. A local platform is easier while
you are still learning the workflow.

## Use the umbrella chart first

The default platform deployment path is the Niuu umbrella chart. Individual
charts are useful when you intentionally split responsibilities, but they should
not be the first deployment story.

Read next:

- [Kubernetes deployment](../operations/kubernetes-deployment.md)
- [Helm charts](../reference/helm-charts.md)
- [Production checklist](../operations/production-checklist.md)

## Keep GitOps as the source of truth

For GitOps-managed resources, make changes in Git and let the controller
reconcile them.

Use Git for:

- Helm values
- chart versions
- labels and annotations
- ExternalSecret wiring
- deployment configuration
- service routing configuration

Do not hand-edit managed deployments and then expect GitOps to understand why.

## Secrets stay in the secret system

Do not commit token values or static Kubernetes secret manifests.

Use the configured secret manager and External Secrets wiring. Workloads should
receive secret values from cluster-local secrets created by that pipeline.

## Route service traffic through the platform path

When a service has an Envoy-facing path, configure callers to use that path.
Do not bypass it by pointing at backend ports or localhost shortcuts.

If Envoy requires auth, configure the caller's auth adapter instead of bypassing
the policy layer.

## Resident assistants in Kubernetes

Long-running assistants should be deployed like other platform workloads:

- config in Git
- secrets through the secret system
- storage explicitly mounted
- labels for cluster and namespace
- discovery metadata for Guild and Observatory
- logs and health visible to operators

For Ravn-based assistants and wardens, prefer the agent deployment path rather
than raw one-off manifests.

## What good looks like

You should be able to answer:

- Which Git commit defines this deployment?
- Which chart owns it?
- Where do its secrets come from?
- Which route do other services use to call it?
- Which labels identify cluster and namespace?
- Where does it appear in Guild or Observatory?

## Next

Use the operations docs when you are ready to deploy for real:

[Kubernetes deployment](../operations/kubernetes-deployment.md)
