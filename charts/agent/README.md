# Niuu Agent

Deploys one long-running Ravn CLI/daemon instance. Use one Helm release per
agent, warden, or valkyrie:

```bash
helm install ravn-k8s-a ./charts/agent \
  --namespace ravn \
  --create-namespace \
  -f ./charts/agent/values-k8s-valkyrie.yaml \
  --set agent.persona=k8s-valkyrie
```

`charts/ravn` is the Ravn API/service chart. This chart is for the CLI/daemon
runtime and defaults to:

```bash
ravn daemon --config /etc/ravn/config.yaml --persona <agent.persona>
```

The default image is `ghcr.io/niuulabs/agent:<chart appVersion>`.
