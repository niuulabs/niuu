# Flock Composition

This guide explains how to configure, author, and operate the **flock composition** system — the mechanism by which named `FlockFlowConfig` definitions are used to give every Ravn sidecar in a cluster a consistent, merged set of LLM and prompt settings.

---

## Overview

A **flock flow** (`FlockFlowConfig`) is a named, reusable persona composition that can be referenced by a pipeline template instead of repeating persona lists inline. When a pipeline dispatches a stage, Tyr:

1. Looks up the named flow via the configured `FlockFlowProvider`.
2. Applies any per-stage `persona_overrides` on top of the flow's persona entries.
3. Packages the merged `workload_config` and passes it to Volundr, which writes the sidecar's `/etc/ravn/config.yaml`.

The three **merge layers** are (last wins per field):

| Layer | Source | What it provides |
|-------|--------|-----------------|
| Base persona defaults | `src/ravn/personas/<name>.yaml` | `primary_alias`, `thinking_enabled`, `iteration_budget`, etc. |
| Flow-level override | `FlockFlowConfig.personas[i]` | Per-persona `llm`, `system_prompt_extra` |
| Stage-level override | `pipeline.stage.persona_overrides` | Final tuning per dispatch context |

> **Security boundary**: `allowed_tools` and `forbidden_tools` are **never** overridable at the flow or stage layer. Attempts are silently dropped and logged at WARN level.

---

## Persona source backends

Each Ravn sidecar needs a way to resolve persona definitions at startup. Three backends are available:

### 1. Filesystem (development default)

Reads persona YAML files from the file system. Default search paths:

- Bundled built-ins: `src/ravn/personas/`
- User overrides: `~/.ravn/personas/`

**When to use**: Local development and single-node deployments where no Kubernetes is involved.

**Configuration** (`ravn.yaml`):

```yaml
persona_source:
  adapter: ravn.adapters.personas.loader.FilesystemPersonaAdapter
  persona_dirs:
    - /app/personas
  include_builtin: true
```

> Switching back to Filesystem mode is the recommended rollback procedure in dev (see [Rollback](#rollback-procedure)).

---

### 2. MountedVolume (recommended for Kubernetes)

Reads personas from a directory mounted into the sidecar container via a Kubernetes ConfigMap volume projection. The `KubernetesConfigMapPersonaRegistry` adapter (running in Volundr) owns **writes**; the `MountedVolumePersonaAdapter` (running in each sidecar) owns **reads**.

**Characteristics**:

- No in-process caching — every call re-scans the mount path, so kubelet ConfigMap updates are visible within one sync cycle (~60 s).
- Overlay paths: `builtin` → `tenant` → per-flock override, later entries win by name.
- Follows `..data` symlinks (required for projected ConfigMaps).

**Helm values** (`values.yaml`):

```yaml
personaSource:
  mode: mountedVolume
  mountedVolume:
    configMapName: ravn-personas   # ConfigMap written by Volundr
    builtinMountPath: /mnt/personas/builtin
    tenantMountPath: /mnt/personas/tenant
```

**Full installation walkthrough**:

```bash
helm upgrade --install volundr charts/volundr \
  --set personaSource.mode=mountedVolume \
  --set personaSource.mountedVolume.configMapName=ravn-personas \
  --namespace volundr --create-namespace
```

Verify the ConfigMap was created:

```bash
kubectl get configmap ravn-personas -n volundr -o yaml
```

---

### 3. HTTP (multi-cluster / cross-namespace)

Each sidecar pulls personas from the Volundr REST API using a PAT (Personal Access Token) mounted as a Kubernetes secret. This backend is ideal when:

- Sidecars run in a different cluster or namespace from Volundr.
- You want all personas served from a single central registry with no ConfigMap projection delay.

**Characteristics**:

- In-memory LRU cache with configurable TTL (default 60 s) to avoid hammering Volundr.
- Fail-closed: on network errors the last cached value is returned; `None` if nothing is cached.
- Auth: `RAVN_VOLUNDR_TOKEN` env var → `Authorization: Bearer <token>`.

**Helm values**:

```yaml
personaSource:
  mode: http
  http:
    baseUrl: http://volundr.volundr.svc.cluster.local:8080
    tokenSecretName: ravn-volundr-token  # k8s Secret holding the PAT
    cacheTtlSeconds: 60
```

**Full installation walkthrough**:

1. Issue a PAT via the Volundr API or admin UI.
2. Create the secret:
   ```bash
   kubectl create secret generic ravn-volundr-token \
     --from-literal=token=<your-pat-value> \
     -n volundr
   ```
3. Install the chart:
   ```bash
   helm upgrade --install volundr charts/volundr \
     --set personaSource.mode=http \
     --set "personaSource.http.baseUrl=http://volundr:8080" \
     --set personaSource.http.tokenSecretName=ravn-volundr-token \
     --namespace volundr --create-namespace
   ```

---

## FlockFlow provider configuration

The flock flow store is also pluggable. Select the adapter in your Helm values or Tyr config:

### ConfigFlockFlowProvider (in-process / testing)

Stores flows in memory. Zero persistence — flows are lost on restart. Use only for development and tests.

```yaml
flockFlows:
  adapter: tyr.adapters.flows.config.ConfigFlockFlowProvider
```

### KubernetesConfigMapFlockFlowProvider (recommended for Kubernetes)

Reads and writes flows from a ConfigMap in the cluster.

```yaml
flockFlows:
  adapter: tyr.adapters.flows.configmap.KubernetesConfigMapFlockFlowProvider
  namespace: tyr
  configmap_name: flock-flows
```

Ensure the Tyr ServiceAccount has `get`, `patch`, `update`, and `watch` on ConfigMaps in the target namespace (see `charts/volundr/templates/rbac.yaml`).

---

## Authoring a flock flow

### YAML example

```yaml
# charts/volundr/config/flows/code-review-flow.yaml
name: code-review-flow
description: Standard parallel code review with security audit

personas:
  - name: reviewer
    llm:
      model: claude-opus-4-6
      thinking_enabled: false
    system_prompt_extra: |
      Focus on correctness, security vulnerabilities, and test coverage.
    iteration_budget: 25

  - name: security-auditor
    llm:
      model: claude-sonnet-4-6
    system_prompt_extra: |
      Audit for OWASP Top 10, injection, and privilege escalation vectors.
    iteration_budget: 20

  - name: coordinator
    llm:
      model: claude-opus-4-6

mesh_transport: nng
mimir_hosted_url: ""
sleipnir_publish_urls: []
max_concurrent_tasks: 3
```

### REST API

Create or update a flow:

```bash
curl -X POST http://localhost:8080/api/v1/flock-flows \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $RAVN_VOLUNDR_TOKEN" \
  -d '{
    "name": "code-review-flow",
    "description": "Standard code review",
    "personas": [
      {
        "name": "reviewer",
        "llm": { "model": "claude-opus-4-6", "thinking_enabled": false },
        "system_prompt_extra": "Focus on security.",
        "iteration_budget": 25
      }
    ]
  }'
```

List flows:

```bash
curl http://localhost:8080/api/v1/flock-flows \
  -H "Authorization: Bearer $RAVN_VOLUNDR_TOKEN"
```

Delete a flow:

```bash
curl -X DELETE http://localhost:8080/api/v1/flock-flows/code-review-flow \
  -H "Authorization: Bearer $RAVN_VOLUNDR_TOKEN"
```

---

## Pipeline template examples

### Reference a flow from a pipeline template

```yaml
name: "Code review: {event.repo}#{event.pr_number}"
feature_branch: "{event.branch}"
base_branch: "{event.base_branch}"
repos:
  - "{event.repo}"

flock_flow: code-review-flow          # named flow to resolve

stages:
  - name: parallel-review
    parallel:
      - persona: reviewer
        prompt: "Review the diff at {event.diff_url}"
        persona_overrides:
          llm:
            thinking_enabled: true    # stage-level override
          system_prompt_extra: |
            Production-critical path — be especially thorough.
      - persona: security-auditor
        prompt: "Audit {event.diff_url} for security issues"
    fan_in: all_must_pass

  - name: final-approval
    gate: human
    condition: "stages.parallel-review.verdict == pass"
```

**Merge precedence** for the `reviewer` persona in this template:

1. Built-in `reviewer.yaml` defaults (`thinking_enabled: false`, `primary_alias: balanced`)
2. Flow override (`model: claude-opus-4-6`, flow `system_prompt_extra`)
3. Stage override (`thinking_enabled: true`, stage `system_prompt_extra` appended)

Final effective config:
```yaml
llm:
  model: claude-opus-4-6
  thinking_enabled: true
system_prompt_extra: |
  Focus on correctness, security vulnerabilities, and test coverage.

  Production-critical path — be especially thorough.
```

---

## Debugging

### Read /etc/ravn/config.yaml from a sidecar

```bash
# Find a sidecar pod
kubectl get pods -l app.kubernetes.io/component=ravn-sidecar -n volundr

# Read the effective config
kubectl exec <pod-name> -c ravn-sidecar -n volundr -- cat /etc/ravn/config.yaml
```

The file is written by Volundr at pod startup and reflects the fully merged persona config. Check:

- `persona.llm.model` — the effective model alias
- `persona.llm.thinking_enabled` — extended thinking flag
- `persona.system_prompt_extra` — concatenated prompt additions

### Verify the sidecar startup log

The ravn sidecar logs the effective config at startup. Look for the line:

```
INFO ravn.startup: loaded persona='reviewer' model='claude-opus-4-6' thinking=True budget=25
```

```bash
kubectl logs <pod-name> -c ravn-sidecar -n volundr | grep -E "loaded persona|effective"
```

### Check ConfigMap projection latency (MountedVolume mode)

Kubernetes syncs projected ConfigMaps within **~60 s** by default (controlled by `--sync-frequency` on the kubelet). If a persona edit is not visible on the sidecar:

1. Check when the ConfigMap was last updated:
   ```bash
   kubectl get configmap ravn-personas -n volundr \
     -o jsonpath='{.metadata.creationTimestamp}'
   ```
2. Check the kubelet sync period on the node:
   ```bash
   kubectl get configmap kubelet-config -n kube-system -o yaml | grep syncFrequency
   ```
3. If the edit is more than 90 s old and still not visible, check RBAC (see [Troubleshooting](troubleshooting.md#persona-edit-didnt-reach-sidecar)).

### Verify ConfigMap content directly

```bash
kubectl get configmap ravn-personas -n volundr \
  -o jsonpath='{.data.reviewer\.yaml}'
```

---

## Rollback procedure

To revert to the `Filesystem` backend in development:

1. Update Helm values:
   ```bash
   helm upgrade volundr charts/volundr \
     --set personaSource.mode=filesystem \
     --namespace volundr
   ```
2. The sidecar will restart and read personas from its bundled built-ins.

For production rollback to a previous persona revision, restore the ConfigMap from your GitOps source of truth (the persona definitions should be version-controlled):

```bash
git checkout <previous-sha> -- charts/volundr/config/personas/
kubectl apply -f charts/volundr/config/personas/
```

---

## Security boundary

The following fields are **not overridable** at the flow or stage layer and will be silently dropped with a WARN log if present:

- `allowed_tools`
- `forbidden_tools`

These are security boundaries set by the base persona definition. To change tool access, update the persona YAML directly and redeploy.

Operators should audit any custom flows for attempts to inject security keys, and review the Volundr API access logs for unexpected persona mutations.
