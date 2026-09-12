# Move to shared infrastructure

Use the [Kubernetes deployment guide](../operations/kubernetes-deployment.md)
when you need shared service operation, persistent cluster workloads, or GitOps.
It covers the actual chart hierarchy, rendering, and rollout checks.

First decide which services will run centrally and which targets will execute
sessions. Kubernetes service deployment and session runtime selection are separate
choices. OpenShell can use a Kubernetes compute driver; see
[OpenShell runtime](../operations/openshell-runtime.md) before assuming that a
normal Kubernetes pod specification will work as an OpenShell sandbox template.

Keep configuration and pinned versions in Git. Keep credential values in the
configured secret system. A GitOps reconciler should own the resources it deploys;
apply configuration changes through that repository so later reconciliation does
not silently undo a manual change.
