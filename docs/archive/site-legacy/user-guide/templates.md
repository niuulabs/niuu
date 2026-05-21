# Templates & Profiles

Volundr has a layered configuration system for sessions. This page explains the three levels: profiles, templates, and session definitions.

## Profiles

Profiles are resource specification presets. Think of them as "instance types" for sessions.

They are read-only, loaded from YAML config (or Kubernetes CRDs), and managed by the operator — not the user. A profile defines:

- Model
- CPU and memory limits
- GPU allocation
- MCP servers
- Environment variables

```yaml
profiles:
  - name: standard
    description: "Standard coding session"
    workload_type: session
    model: "claude-sonnet-4-20250514"
    resource_config:
      cpu: "500m"
      memory: "1Gi"
    is_default: true
```

The `is_default: true` profile is used when no profile or template is specified.

## Templates

Templates are workspace blueprints. They combine resource configuration with repositories and setup scripts.

Like profiles, templates are read-only and operator-managed. Templates are what users see in the launch wizard when creating a session.

```yaml
templates:
  - name: python-project
    description: "Python development workspace"
    workload_type: session
    model: "claude-sonnet-4-20250514"
    resource_config:
      cpu: "2"
      memory: "4Gi"
    repos:
      - url: "https://github.com/org/repo"
        branch: main
    setup_scripts:
      - "uv sync --dev"
    is_default: false
```

A template can do everything a profile can, plus:

- Clone repositories on startup
- Run setup scripts after clone
- Pre-configure the workspace for a specific project

## When to use which

**Profiles** — Use when you just need resource allocation presets without any repo or setup configuration. Good for general-purpose sessions where users pick their own repos.

**Templates** — Use when you want full session blueprints. A template gives users a one-click setup for a specific project or workflow, with repos cloned and dependencies installed automatically.

Templates reference profiles internally but can override any value. If a template sets `cpu: "2"` and the underlying profile says `cpu: "500m"`, the template wins.

## Session definitions

Session definitions are Kubernetes CRDs that define pod templates for different agent types (Claude Code, Codex). These sit below profiles and templates in the stack.

Session definitions are infrastructure-level configuration managed via Helm. They specify:

- The Skuld chart version to use
- Container images for each sidecar
- Default Helm values

Most users never interact with session definitions directly. They are set up once by the platform operator and rarely change.

## How it all fits together

```
Session Definition (infrastructure, Helm-managed)
  └── Profile (resource presets, operator-managed)
       └── Template (workspace blueprints, operator-managed)
            └── Preset (user preferences, database-stored)
```

Each layer can override values from the layer below. The most specific configuration wins.
