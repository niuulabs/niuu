# Niuu Agent

Deploys one long-running Ravn CLI/daemon instance. Use one Helm release per
agent or resident:

```bash
helm install ravn-k8s-a ./charts/agent \
  --namespace ravn \
  --create-namespace \
  --set agent.persona=<persona>
```

`charts/ravn` is the Ravn API/service chart. This chart is for the CLI/daemon
runtime and runs:

```bash
ravn daemon --config /etc/ravn/config.yaml --persona <agent.persona>
```

The default image is `ghcr.io/niuulabs/agent:<chart appVersion>`.

Set `learnedToolRunner.enabled=true` to run learned tools as one locked-down
Kubernetes Job per invocation. The chart then creates the least-privilege RBAC
and the two egress NetworkPolicies whose exact selectors Ravn verifies before
every run. The runner image must remain pinned by digest; runtime package
installation and target-specific network reach intentionally fail closed.
