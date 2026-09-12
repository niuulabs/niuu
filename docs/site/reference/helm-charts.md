# Helm chart reference

The umbrella chart lives at `charts/niuu`. It declares local chart dependencies,
so a source checkout needs `helm dependency update ./charts/niuu` before rendering
when packaged dependencies are absent.

## Chart responsibilities

| Chart | Purpose |
| --- | --- |
| `niuu` | Umbrella dependency selection and shared ingress routing |
| `volundr` | Forge service, workspace/runtime backend configuration, web surfaces |
| `niuu-shared` | Shared platform services |
| `ting` | Workflow service |
| `guild` | Service instance groups, discovery, and routing |
| `bifrost` | Model gateway |
| `observatory` | Topology and observability |
| `mimir` | Knowledge service; the umbrella can include multiple aliases |
| `ravn` | Ravn service |
| `agent` | Agent workload deployment; inspect separately from the umbrella |

The exact enabled dependencies and versions are in the selected chart's
`Chart.yaml`. Do not assume that a chart existing in the repository means the
umbrella deploys it.

## Inspect before setting values

```bash
helm show chart ./charts/niuu
helm show values ./charts/volundr
```

Inside umbrella values, the Völundr fields are nested under `volundr`. Within the
standalone chart they are at the root. The same distinction applies to the other
subcharts. Aliases such as `mimir-shared` have their own values trees.

A values file can contain unused keys without producing the intended workload
change. Inspect the rendered manifest for the actual environment variable,
configuration file, secret reference, or pod field you meant to change.
See [deployment](../operations/kubernetes-deployment.md) for the render/apply flow.
