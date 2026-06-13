# Helm Charts

Deploy Niuu services with the charts under `charts/`.

## Main charts

| Chart | Purpose |
| --- | --- |
| `charts/niuu` | Umbrella chart for the platform |
| `charts/volundr` | Standalone Völundr deployment |
| `charts/ting` | Workflow service |
| `charts/ravn` | Assistant runtime |
| `charts/agent` | Ravn CLI/daemon agent runtime |
| `charts/mimir` | Knowledge service |
| `charts/bifrost` | Model gateway |
| `charts/guild` | Runtime registry |
| `charts/observatory` | Topology and observability surface |

## Default path

Use the umbrella chart unless you intentionally need to deploy individual services.
