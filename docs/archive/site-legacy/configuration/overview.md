# Configuration Overview

Volundr loads configuration from multiple sources. Higher priority sources override lower ones.

**Priority order (highest to lowest):**

1. **Constructor arguments** — testing only
2. **Environment variables** — double underscore nesting: `DATABASE__HOST=postgres.local`
3. **YAML config file** — `./config.yaml` or `/etc/volundr/config.yaml`
4. **`/run/secrets` files** — Kubernetes secrets mounted as files

## Two Config Contexts

There are two ways to configure Volundr depending on how you run it.

**Config file** (`config.yaml` / `config.yaml.example`) is for running from source or local CLI. Uses snake_case throughout.

**Helm values** (`values.yaml`) is for Kubernetes deployments. The chart generates a ConfigMap mounted at `/etc/volundr/config.yaml`. Uses camelCase.

The naming differs between them. Helm uses camelCase (`podManager`), config.yaml uses snake_case (`pod_manager`). The chart templates handle the translation.

## Dynamic Adapter Pattern

Most infrastructure components are configured by specifying a fully-qualified Python class path and constructor kwargs. You can swap backends without code changes — just update the config.

```yaml
# Example: configure pod manager
pod_manager:
  adapter: "volundr.adapters.outbound.flux.FluxPodManager"
  kwargs:
    namespace: "default"
    chart_name: "skuld"
```

Adding a new adapter = write the class + update config. No code changes in the container.

See [Dynamic Adapters](adapters.md) for full details on how the pattern works.
