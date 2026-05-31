# Storage

**Port:** StoragePort

| Adapter | Description |
|---------|-------------|
| `InMemoryStorageAdapter` | Development — tracks PVC names without creating K8s resources |
| `K8sStorageAdapter` | Production — creates and manages PVCs via Kubernetes API |

## Two Volume Types

**Session workspace** — Per-session PVC mounted at `/volundr/sessions`. Created when a session starts, archived on stop, deleted on session delete.

**Home volume** — Per-user PVC mounted at `/volundr/home`. Persists across sessions. Stores user config, Claude credentials, shell history.

## Helm Configuration

```yaml
storage:
  sessions:
    enabled: true
    storageClass: longhorn
    accessMode: ReadWriteMany
    size: 1Gi
    mountPath: /volundr/sessions
  home:
    enabled: true
    storageClass: longhorn
    accessMode: ReadWriteMany
    size: 1Gi
    mountPath: /volundr/home
```

## K8s Adapter (config.yaml)

```yaml
storageAdapter:
  adapter: "volundr.adapters.outbound.k8s_storage_adapter.K8sStorageAdapter"
  kwargs:
    namespace: "volundr-sessions"
    home_storage_class: "longhorn"
    workspace_storage_class: "longhorn"
    workspace_size_gb: 2
```

## Storage Class Requirements

Session PVCs need ReadWriteMany if multiple containers access them simultaneously. Home PVCs need ReadWriteMany for cross-session access.

Compatible storage classes:

- **Longhorn** — recommended for bare-metal and edge
- **NFS-based provisioners** — works everywhere
- **Cloud RWX classes** — EFS (AWS), Azure Files, Filestore (GCP)

Single-node clusters can use ReadWriteOnce, but this limits session pods to the node where the PVC is bound.
