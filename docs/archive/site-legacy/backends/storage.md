# Storage

Volundr manages Kubernetes PersistentVolumeClaims for user home directories and per-session workspaces.

## Adapters

| Adapter | Description |
|---------|-------------|
| `InMemoryStorageAdapter` | No-op for development (default) |
| `K8sStorageAdapter` | Creates and manages PVCs via Kubernetes API |

### Kubernetes storage

```yaml
storage:
  adapter: "volundr.adapters.outbound.k8s_storage_adapter.K8sStorageAdapter"
  kwargs:
    namespace: "volundr-sessions"
    home_storage_class: "volundr-home"
```

## PVC types

| Type | Lifecycle | Purpose |
|------|-----------|---------|
| Home PVC | Per-user, persistent | User home directory, SSH keys, shell config |
| Workspace PVC | Per-session | Session workspace, cloned repos, build artifacts |

## Storage quotas

Each tenant defines storage limits:

- `max_storage_gb` — total storage across all workspaces

Users get a home PVC on first provisioning. Workspace PVCs are created per session and can be archived (soft delete) or permanently deleted.

## Kyverno isolation

PVCs are labeled with owner, session, and tenant IDs. A Kyverno policy enforces that pods can only mount PVCs matching their labels, preventing cross-tenant access.
